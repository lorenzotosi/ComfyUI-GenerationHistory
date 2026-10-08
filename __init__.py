from .generation_history import GenerationHistory

NODE_CLASS_MAPPINGS = {"GenerationHistory": GenerationHistory}
NODE_DISPLAY_NAME_MAPPINGS = {"GenerationHistory": "Generation History"}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
