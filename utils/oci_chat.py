"""
OCI Cohere chat completion for RAG answers.
Uses us-chicago-1 for Generative AI Inference by default (override via OCI_REGION).
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_OCI_REGION = "us-chicago-1"


def chat_with_context(
    config: dict,
    compartment_id: str,
    context: str,
    question: str,
    model_id: str = "cohere.command-r-08-2024",  # or try: meta.llama-3.1-70b-instruct
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> Optional[str]:
    """
    Call OCI Cohere chat with context + question (RAG-style).
    Returns the model reply text or None on error.
    """
    import oci
    from oci.generative_ai_inference import GenerativeAiInferenceClient
    from oci.generative_ai_inference.models import (
        ChatDetails,
        CohereChatRequest,
        OnDemandServingMode,
    )

    prompt = (
        "Based on the following context from our documents:\n\n"
        f"{context}\n\n"
        f"Question: {question}\n\n"
        "Instructions:\n"
        "1. Answer in one short paragraph based only on the context.\n"
        "2. If the context does not contain the answer, say so.\n"
        "3. IMPORTANT: Respond in the SAME LANGUAGE as the question. "
        "If the question is in Korean, answer in Korean. If in Japanese, answer in Japanese. Etc."
    )

    try:
        region = os.getenv("OCI_REGION", DEFAULT_OCI_REGION)
        client_config = {**config, "region": region}
        client = GenerativeAiInferenceClient(client_config)
        serving_mode = OnDemandServingMode(
            serving_type="ON_DEMAND",
            model_id=model_id,
        )
        chat_request = CohereChatRequest(
            api_format="COHERE",
            message=prompt,
            chat_history=[],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        details = ChatDetails(
            compartment_id=compartment_id,
            serving_mode=serving_mode,
            chat_request=chat_request,
        )
        response = client.chat(details)
        # Extract text from response (structure may vary by SDK version)
        if hasattr(response, "data"):
            data = response.data
            if hasattr(data, "chat_response"):
                chat_resp = data.chat_response
                if hasattr(chat_resp, "message") and hasattr(chat_resp.message, "content"):
                    return chat_resp.message.content
                if hasattr(chat_resp, "content"):
                    return chat_resp.content
                if hasattr(chat_resp, "text"):
                    return chat_resp.text
            if hasattr(data, "content"):
                return data.content
        if hasattr(response, "content"):
            return response.content
        logger.warning("Unexpected chat response shape: %s", type(response))
        return None
    except Exception as e:
        logger.error("OCI chat failed: %s", e)
        return None
