# app.py — Ivy Reasoning Space
# Loads Qwen2.5-1.5B-Instruct for Layer 3 semantic checks:
# rules, scam, politics, and grooming detection (grooming's primary
# path — Groq heavy model is the emergency fallback only if this
# Space is unreachable, not the normal path anymore).
# Called by core/semantic.py's _qwen_reasoning_call.

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
import os

app = FastAPI()

MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"
MAX_NEW_TOKENS_CAP = 400  # hard server-side cap, never trust client params alone

print("Loading Qwen 1.5B... this happens once on startup.")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.float32,  # CPU-friendly
)
model.eval()


def generate_reply(prompt: str, max_new_tokens: int = 300, temperature: float = 0.1) -> str:
    max_new_tokens = min(max_new_tokens, MAX_NEW_TOKENS_CAP)

    messages = [{"role": "user", "content": prompt}]
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(text, return_tensors="pt")

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=temperature > 0,
            pad_token_id=tokenizer.eos_token_id,
        )

    generated = output_ids[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


@app.get("/")
async def health():
    return {"status": "Ivy reasoning model is alive."}


@app.post("/")
async def chat(request: Request):
    """
    Expects: {"inputs": "prompt text", "parameters": {"max_new_tokens": 200, "temperature": 0.7}}
    Returns: {"generated_text": "response"}
    Matches the shape Ivy's hf_chat.py expects from _call_hf_space.
    """
    try:
        body = await request.json()
        prompt = body.get("inputs", "")
        params = body.get("parameters", {}) or {}

        if not prompt:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing 'inputs' field."},
            )

        # Handle conversational format (past_user_inputs/generated_responses)
        # by flattening into a single prompt if that shape is sent.
        if isinstance(prompt, dict):
            history = ""
            past_user = prompt.get("past_user_inputs", [])
            past_ivy = prompt.get("generated_responses", [])
            for u, i in zip(past_user, past_ivy):
                history += f"User: {u}\nIvy: {i}\n"
            current = prompt.get("text", "")
            prompt = f"{history}User: {current}\nIvy:"

        max_new_tokens = params.get("max_new_tokens", 200)
        temperature = params.get("temperature", 0.1)

        reply = generate_reply(prompt, max_new_tokens, temperature)
        return JSONResponse(content={"generated_text": reply})

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )
