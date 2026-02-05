"""
OCI Cohere embedding via Generative AI Inference.
init_client(config) -> client, get_embeddings(client, compartment_id, texts) -> list of vectors.

Uses Cohere Embed 4 to match DB ingest (doc_chunks). Generative AI Inference region defaults to us-chicago-1 (Embed 4 on-demand there). Override via env OCI_REGION or OCI_EMBED_MODEL_ID if needed.
"""

import os
import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Same as DB ingest (OCI_COHERE_EMBED profile): Cohere Embed 4
DEFAULT_EMBED_MODEL = "cohere.embed-v4.0"


# Region for Generative AI Inference (Embed 4 on-demand in us-chicago-1)
DEFAULT_OCI_REGION = "us-chicago-1"


def init_client(config: dict) -> Any:
    """Initialize OCI Generative AI Inference client (for embeddings). Uses us-chicago-1 by default so Embed 4 is on-demand."""
    from oci.generative_ai_inference import GenerativeAiInferenceClient
    region = os.getenv("OCI_REGION", DEFAULT_OCI_REGION)
    client_config = {**config, "region": region}
    return GenerativeAiInferenceClient(client_config)


def get_embeddings(
    client: Any,
    compartment_id: str,
    texts: List[str],
    model_id: Optional[str] = None,
) -> List[List[float]]:
    """
    Get embeddings for a list of texts using OCI Cohere embed model.
    Returns list of embedding vectors (each a list of floats).
    model_id: defaults to OCI_EMBED_MODEL_ID env var or cohere.embed-v4.0 (same as DB ingest).
    """
    from oci.generative_ai_inference.models import (
        EmbedTextDetails,
        OnDemandServingMode,
    )

    if not texts:
        return []

    model_id = model_id or os.getenv("OCI_EMBED_MODEL_ID") or DEFAULT_EMBED_MODEL

    try:
        serving_mode = OnDemandServingMode(
            serving_type="ON_DEMAND",
            model_id=model_id,
        )
        details = EmbedTextDetails(
            compartment_id=compartment_id,
            serving_mode=serving_mode,
            inputs=texts,
            input_type=EmbedTextDetails.INPUT_TYPE_SEARCH_QUERY,
        )
        response = client.embed_text(details)
        # Response typically has .data.embeddings (list of lists)
        if hasattr(response, "data") and hasattr(response.data, "embeddings"):
            return response.data.embeddings
        if hasattr(response, "embeddings"):
            return response.embeddings
        logger.warning("Unexpected embed response shape: %s", type(response))
        return []
    except Exception as e:
        logger.error("OCI embed_text failed: %s", e)
        raise
