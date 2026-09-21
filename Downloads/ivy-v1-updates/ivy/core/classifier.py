# core/classifier.py
# Ivy Bot - Layer 2: Multilingual ONNX Toxicity Classifier
# Uses onnx-community/distilbert-multilingual-toxicity-classifier-ONNX
# hosted on HuggingFace Space, with local ONNX fallback.
# Zero Groq tokens consumed at this layer.

import asyncio
import aiohttp
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("ivy.classifier")

from config import THRESHOLDS, HF_MODERATION_SPACE_URL, HF_API_KEY


# ========================
# CLASSIFICATION RESULT
# ========================

@dataclass
class ClassifierResult:
    score: float                    # 0.0 to 1.0 toxicity probability
    label: str                      # "toxic" | "not_toxic"
    source: str                     # "hf_space" | "hf_api" | "local_onnx" | "failed"
    action: str                     # "block" | "flag" | "pass"
    raw_scores: dict = None         # full label->score map


def _determine_action(score: float) -> str:
    if score >= THRESHOLDS.block:
        return "block"
    elif score >= THRESHOLDS.flag:
        return "flag"
    return "pass"


# ========================
# SHARED HTTP SESSION
# One session reused across all requests.
# Created lazily, never closed (lives for bot lifetime).
# ========================

_http_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8)
        )
    return _http_session


# ========================
# LOCAL ONNX INFERENCE
# Runs DistilBERT ONNX model locally if files are present.
# Completely offline, zero API calls.
# ========================

class _LocalONNXClassifier:
    """
    Singleton local ONNX classifier.
    Lazy loaded on first use to keep startup fast.
    Falls back gracefully if model files aren't present.
    """
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.session = None
        self.tokenizer = None
        self.failed = False
        self.model_dir = "./onnx-classifier"
        self.id2label = {0: "not_toxic", 1: "toxic"}

    def load(self) -> bool:
        if self.session is not None:
            return True
        if self.failed:
            return False

        try:
            import onnxruntime as ort
            import os

            if not os.path.isdir(self.model_dir):
                print(
                    f"⚠️  [Classifier] Local ONNX dir '{self.model_dir}' "
                    f"not found. Skipping local inference."
                )
                self.failed = True
                return False

            model_path = os.path.join(self.model_dir, "model.onnx")
            vocab_path = os.path.join(self.model_dir, "vocab.txt")

            if not os.path.exists(model_path):
                log.warning("  [Classifier] model.onnx not found.")
                self.failed = True
                return False

            self.session = ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"]
            )

            # Load tokenizer
            if os.path.exists(vocab_path):
                self.tokenizer = _SimpleTokenizer(vocab_path)
                log.info(" [Classifier] Local ONNX loaded with SimpleTokenizer.")
            else:
                try:
                    from transformers import AutoTokenizer
                    self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
                    log.info(" [Classifier] Local ONNX loaded with AutoTokenizer.")
                except Exception as e:
                    print(f"❌ [Classifier] Tokenizer load failed: {e}")
                    self.failed = True
                    return False

            # Load label map from config.json if present
            import json
            config_path = os.path.join(self.model_dir, "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                    if "id2label" in cfg:
                        self.id2label = {
                            int(k): v for k, v in cfg["id2label"].items()
                        }

            return True

        except Exception as e:
            print(f"❌ [Classifier] Local ONNX load failed: {e}")
            self.failed = True
            return False

    def _softmax(self, x: np.ndarray) -> np.ndarray:
        e_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
        return e_x / e_x.sum(axis=-1, keepdims=True)

    def predict(self, text: str) -> Optional[ClassifierResult]:
        if not self.load():
            return None

        try:
            # Tokenize
            if hasattr(self.tokenizer, "encode_plus"):
                inputs = self.tokenizer(
                    text,
                    truncation=True,
                    padding=True,
                    max_length=128,
                    return_tensors="np",
                )
                input_ids = inputs["input_ids"][0].tolist()
                attention_mask = inputs["attention_mask"][0].tolist()
            else:
                input_ids, attention_mask = self.tokenizer.encode(
                    text, max_length=128
                )

            # Build ONNX inputs
            onnx_input_names = [x.name for x in self.session.get_inputs()]
            ort_inputs = {}
            if "input_ids" in onnx_input_names:
                ort_inputs["input_ids"] = np.array(
                    [input_ids], dtype=np.int64
                )
            if "attention_mask" in onnx_input_names:
                ort_inputs["attention_mask"] = np.array(
                    [attention_mask], dtype=np.int64
                )
            if "token_type_ids" in onnx_input_names:
                ort_inputs["token_type_ids"] = np.array(
                    [[0] * len(input_ids)], dtype=np.int64
                )

            logits = self.session.run(None, ort_inputs)[0]
            probs = self._softmax(logits)[0]

            raw_scores = {
                self.id2label.get(i, f"LABEL_{i}"): float(p)
                for i, p in enumerate(probs)
            }

            pred_idx = int(np.argmax(logits, axis=-1)[0])
            pred_label = self.id2label.get(pred_idx, f"LABEL_{pred_idx}")

            # Find toxic score specifically
            tox_score = 0.0
            for label, score in raw_scores.items():
                if "toxic" in label.lower() and "not" not in label.lower():
                    tox_score = score
                    break
            else:
                tox_score = float(probs[1]) if len(probs) > 1 else float(probs[0])

            return ClassifierResult(
                score=tox_score,
                label=pred_label,
                source="local_onnx",
                action=_determine_action(tox_score),
                raw_scores=raw_scores,
            )

        except Exception as e:
            log.warning(f"  [Classifier] Local inference error: {e}")
            return None


# ========================
# SIMPLE WORDPIECE TOKENIZER
# Pure Python. No transformers dependency.
# Used when vocab.txt is present locally.
# ========================

class _TrieNode:
    """Single node in the vocab Trie."""
    __slots__ = ("children", "token_id", "is_end")

    def __init__(self):
        self.children: dict[str, "_TrieNode"] = {}
        self.token_id: Optional[int] = None
        self.is_end: bool = False


class _VocabTrie:
    """
    Trie structure for O(L) WordPiece substring lookup
    instead of O(L²) nested while loops.
    L = word length.
    """
    def __init__(self):
        self.root = _TrieNode()

    def insert(self, token: str, token_id: int):
        node = self.root
        for ch in token:
            if ch not in node.children:
                node.children[ch] = _TrieNode()
            node = node.children[ch]
        node.is_end = True
        node.token_id = token_id

    def longest_prefix(self, text: str, start: int) -> tuple[Optional[str], Optional[int]]:
        """
        Returns the longest matching token starting at `start`.
        Returns (token_string, token_id) or (None, None) if no match.
        """
        node = self.root
        last_match_end = -1
        last_token_id = None
        last_substr = None

        for i in range(start, len(text)):
            ch = text[i]
            if ch not in node.children:
                break
            node = node.children[ch]
            if node.is_end:
                last_match_end = i + 1
                last_token_id = node.token_id
                last_substr = text[start:last_match_end]

        return last_substr, last_token_id


class _SimpleTokenizer:
    """
    WordPiece tokenizer backed by a Trie for efficient substring matching.
    Pure Python, no transformers dependency.
    """
    def __init__(self, vocab_path: str):
        self.vocab: dict[str, int] = {}
        self.trie = _VocabTrie()

        with open(vocab_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                token = line.strip()
                if token:
                    self.vocab[token] = idx
                    self.trie.insert(token, idx)

        self.unk_id = self.vocab.get("[UNK]", 100)
        self.cls_id = self.vocab.get("[CLS]", 101)
        self.sep_id = self.vocab.get("[SEP]", 102)
        self.pad_id = self.vocab.get("[PAD]", 0)

    def _tokenize_word(self, word: str) -> list[str]:
        """
        WordPiece tokenize a single word using Trie lookup.
        O(L) per position instead of O(L²) nested loops.
        """
        if word in self.vocab:
            return [word]

        tokens = []
        start = 0
        while start < len(word):
            # Build the candidate: first subword is plain, rest get "##" prefix
            candidate = word[start:] if start == 0 else "##" + word[start:]

            # Walk Trie to find longest matching prefix
            substr, token_id = self.trie.longest_prefix(candidate, 0)

            if substr is None:
                return ["[UNK]"]

            tokens.append(substr)
            # Advance by the length of matched chars (strip "##" prefix offset)
            start += len(substr) - (2 if start > 0 else 0)

        return tokens

    def encode(self, text: str, max_length: int = 128):
        import re
        text = text.lower()
        text = re.sub(r"([.,!?\"':;()\[\]{}—-])", r" \1 ", text)
        words = text.split()

        token_ids = [self.cls_id]
        for word in words:
            if len(token_ids) >= max_length - 1:
                break
            for token in self._tokenize_word(word):
                token_ids.append(self.vocab.get(token, self.unk_id))
                if len(token_ids) >= max_length - 1:
                    break
        token_ids.append(self.sep_id)

        attention_mask = [1] * len(token_ids)
        while len(token_ids) < max_length:
            token_ids.append(self.pad_id)
            attention_mask.append(0)

        return token_ids[:max_length], attention_mask[:max_length]


# ========================
# HUGGINGFACE SPACE INFERENCE
# Primary cloud route — hits the hosted ONNX Space first.
# ========================

async def _classify_via_hf_space(text: str) -> Optional[ClassifierResult]:
    """Calls the HuggingFace Space hosting the ONNX classifier."""
    if not HF_MODERATION_SPACE_URL:
        return None

    try:
        session = await _get_session()
        headers = {}
        if HF_API_KEY:
            headers["Authorization"] = f"Bearer {HF_API_KEY}"

        async with session.post(
            HF_MODERATION_SPACE_URL,
            json={"inputs": text},
            headers=headers,
        ) as resp:
            if resp.status != 200:
                log.warning(f"  [Classifier HF Space] HTTP {resp.status}")
                return None

            data = await resp.json()

            # HF inference API format:
            # [[{"label": "not_toxic", "score": 0.95}, {"label": "toxic", "score": 0.05}]]
            if isinstance(data, list) and data:
                inner = data[0] if isinstance(data[0], list) else data
                raw_scores = {
                    item["label"]: item["score"] for item in inner
                }

                tox_score = 0.0
                for label, score in raw_scores.items():
                    if "toxic" in label.lower() and "not" not in label.lower():
                        tox_score = score
                        break

                pred_label = (
                    "toxic" if tox_score >= THRESHOLDS.block else "not_toxic"
                )

                return ClassifierResult(
                    score=tox_score,
                    label=pred_label,
                    source="hf_space",
                    action=_determine_action(tox_score),
                    raw_scores=raw_scores,
                )

    except asyncio.TimeoutError:
        log.warning("  [Classifier HF Space] Request timed out.")
    except Exception as e:
        log.warning(f"  [Classifier HF Space] Error: {e}")

    return None


# ========================
# HUGGINGFACE API FALLBACK
# Secondary cloud route — hits HF Inference API directly.
# Used if Space is down or not configured.
# ========================

_HF_API_URL = (
    "https://api-inference.huggingface.co/models/"
    "onnx-community/distilbert-multilingual-toxicity-classifier-ONNX"
)


async def _classify_via_hf_api(text: str) -> Optional[ClassifierResult]:
    """Calls HuggingFace Inference API directly as fallback."""
    try:
        session = await _get_session()
        headers = {}
        if HF_API_KEY:
            headers["Authorization"] = f"Bearer {HF_API_KEY}"

        async with session.post(
            _HF_API_URL,
            json={"inputs": text},
            headers=headers,
        ) as resp:
            if resp.status != 200:
                log.warning(f"  [Classifier HF API] HTTP {resp.status}")
                return None

            data = await resp.json()

            if isinstance(data, dict) and "error" in data:
                log.warning(f"  [Classifier HF API] Model error: {data['error']}")
                return None

            if isinstance(data, list) and data:
                inner = data[0] if isinstance(data[0], list) else data
                raw_scores = {
                    item["label"]: item["score"] for item in inner
                }

                tox_score = 0.0
                for label, score in raw_scores.items():
                    if "toxic" in label.lower() and "not" not in label.lower():
                        tox_score = score
                        break

                pred_label = (
                    "toxic" if tox_score >= THRESHOLDS.block else "not_toxic"
                )

                return ClassifierResult(
                    score=tox_score,
                    label=pred_label,
                    source="hf_api",
                    action=_determine_action(tox_score),
                    raw_scores=raw_scores,
                )

    except asyncio.TimeoutError:
        log.warning("  [Classifier HF API] Request timed out.")
    except Exception as e:
        log.warning(f"  [Classifier HF API] Error: {e}")

    return None


# ========================
# MAIN CLASSIFY FUNCTION
# Called by the moderation pipeline.
# Tries: HF Space → HF API → Local ONNX → None (skip layer)
# Returns None if all sources fail — pipeline moves to Layer 3.
# ========================

async def classify(text: str, timeout: float = 5.0) -> Optional[ClassifierResult]:
    """
    Classify text toxicity using the multilingual ONNX model.

    Fallback chain (v1 — local-first, since no HF Space capacity is
    available right now, all 3 accounts committed to Breezy):
    1. Local ONNX (primary — runs directly on Wispbyte, no external
       dependency, no cold-start, no account/quota concerns)
    2. HuggingFace Space (only relevant if HF_MODERATION_SPACE_URL is
       ever set again — becomes a real upgrade path once Space
       capacity exists, but not required for v1)
    3. HuggingFace direct Inference API (last resort — has real free
       tier limits, not meant to be relied on for production volume)
    4. None — layer skipped, pipeline continues to Layer 3

    Never raises. Always returns ClassifierResult or None.
    """

    # 1. Local ONNX first — this is primary for v1, not a fallback.
    # Runs in executor to avoid blocking the event loop.
    local = _LocalONNXClassifier.get()
    if not local.failed:
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, local.predict, text),
                timeout=3.0,
            )
            if result:
                return result
        except asyncio.TimeoutError:
            log.warning("  [Classifier] Local ONNX timed out.")
        except Exception as e:
            log.warning(f"  [Classifier] Local ONNX error: {e}")

    # 2. HF Space — only tried if configured. Skipped entirely and
    # silently if HF_MODERATION_SPACE_URL isn't set, which is expected
    # for v1. No warning noise for an intentionally-unused path.
    if HF_MODERATION_SPACE_URL:
        try:
            result = await asyncio.wait_for(
                _classify_via_hf_space(text), timeout=timeout
            )
            if result:
                return result
        except asyncio.TimeoutError:
            log.warning("  [Classifier] HF Space timed out, trying HF API...")

    # 3. HF direct API — last resort, real free-tier limits apply.
    # Gated behind having a key configured, same treatment as the Space
    # check above. Without a key this call is essentially guaranteed to
    # fail or rate-limit against a community model that likely isn't
    # even warm — that's real latency (up to `timeout` seconds) burned
    # on every suspicious message for a near-certain failure. Skipping
    # straight to Layer 3 in that case isn't giving up early, it's
    # not pretending a dead path is a real fallback.
    if HF_API_KEY:
        try:
            result = await asyncio.wait_for(
                _classify_via_hf_api(text), timeout=timeout
            )
            if result:
                return result
        except asyncio.TimeoutError:
            log.warning("  [Classifier] HF API timed out.")

    # 4. All sources failed — return None, pipeline skips to Layer 3
    log.warning("  [Classifier] All classifier sources failed. Skipping to Layer 3.")
    return None
