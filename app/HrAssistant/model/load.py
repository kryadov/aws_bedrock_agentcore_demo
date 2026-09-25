import os

from strands.models.bedrock import BedrockModel

# Set in agentcore.json -> runtimes[].envVars
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-sonnet-5")


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials."""
    return BedrockModel(model_id=MODEL_ID)
