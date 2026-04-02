"""MES Intel ML Training Pipeline — Phase 2"""
# Lazy imports to avoid xgboost dylib errors if libomp is not available
def __getattr__(name):
    if name == 'ModelTrainer':
        from .trainer import ModelTrainer
        return ModelTrainer
    elif name == 'FeatureEngine':
        from .features import FeatureEngine
        return FeatureEngine
    elif name == 'WalkForwardValidator':
        from .validator import WalkForwardValidator
        return WalkForwardValidator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ['ModelTrainer', 'FeatureEngine', 'WalkForwardValidator']
