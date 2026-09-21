# app.py — Ivy Moderation Classifier Space
# Loads onnx-community/distilbert-multilingual-toxicity-classifier-ONNX
# Exposes a POST endpoint shaped exactly like HuggingFace's Inference
# API — this matches what core/classifier.py's _classify_via_hf_space
# already expects, so no changes are needed on the bot side once
# HF_MODERATION_SPACE_URL is set to this Space's URL.

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from transformers import AutoTokenizer
from huggingface_hub import hf_hub_download
import onnxruntime as ort
import numpy as np

app = FastAPI()

MODEL_NAME = "onnx-community/distilbert-multilingual-toxicity-classifier-ONNX"

print("Loading tokenizer and ONNX model... this happens once on startup.")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

# NOTE — honest flag, not verified live: this filename is the common
# convention for this model family ("onnx/model.onnx"), but I don't
# have web access from here to confirm it against this exact repo's
# actual file listing. If the Space fails to build with a "file not
# found" error on this line, check the model's "Files" tab on
# huggingface.co and adjust this path to match — that's the one
# external fact this file can't self-verify.
model_path = hf_hub_download(
    repo_id=MODEL_NAME,
    filename="onnx/model.onnx",
)
session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

# NOTE — same honesty flag: this is the standard binary-toxicity
# convention (0=not_toxic, 1=toxic), but double-check against this
# model's actual config.json id2label if scores look inverted once live.
ID2LABEL = {0: "not_toxic", 1: "toxic"}


def softmax(x):
    e_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e_x / e_x.sum(axis=-1, keepdims=True)


def classify_text(text: str):
    inputs = tokenizer(
        text,
        truncation=True,
        padding=True,
        max_length=128,
        return_tensors="np",
    )

    onnx_input_names = [x.name for x in session.get_inputs()]
    ort_inputs = {}
    if "input_ids" in onnx_input_names:
        ort_inputs["input_ids"] = inputs["input_ids"].astype(np.int64)
    if "attention_mask" in onnx_input_names:
        ort_inputs["attention_mask"] = inputs["attention_mask"].astype(np.int64)
    if "token_type_ids" in onnx_input_names and "token_type_ids" in inputs:
        ort_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

    logits = session.run(None, ort_inputs)[0]
    probs = softmax(logits)[0]

    return [
        {"label": ID2LABEL.get(i, f"LABEL_{i}"), "score": float(p)}
        for i, p in enumerate(probs)
    ]


@app.get("/")
async def health():
    return {"status": "Ivy moderation classifier is alive."}


@app.post("/")
async def predict(request: Request):
    """
    Expects: {"inputs": "text to classify"}
    Returns: [[{"label": "not_toxic", "score": 0.95}, {"label": "toxic", "score": 0.05}]]
    This nested-list shape is what classifier.py's HF Space call
    already parses — verified against the live calling code, not
    guessed.
    """
    try:
        body = await request.json()
        text = body.get("inputs", "")
        if not text or not isinstance(text, str):
            return JSONResponse(
                status_code=400,
                content={"error": "Missing or invalid 'inputs' field."},
            )

        results = classify_text(text)
        return JSONResponse(content=[results])

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )
