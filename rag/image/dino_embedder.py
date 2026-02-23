"""
DINOv2 이미지 임베딩 모듈.

Meta의 DINOv2 비전 트랜스포머를 사용하여 이미지를 고차원 벡터로 변환합니다.
모델은 torch.hub를 통해 로드되며, 싱글톤 패턴으로 관리됩니다.
생성된 임베딩은 ChromaDB에 저장되어 시각적 유사도 검색에 활용됩니다.

DINOv2 모델별 임베딩 차원:
    - dinov2_vits14 (ViT-S/14): 384차원, ~22M 파라미터 (기본값)
    - dinov2_vitb14 (ViT-B/14): 768차원, ~86M 파라미터
    - dinov2_vitl14 (ViT-L/14): 1024차원, ~300M 파라미터
"""

import logging
from typing import Optional

import torch
import torch.nn as nn
from torchvision import transforms
from PIL import Image

from config.settings import get_settings

logger = logging.getLogger(__name__)

_model: Optional[nn.Module] = None
_device: Optional[str] = None
_transform: Optional[transforms.Compose] = None


def get_dino_model() -> tuple[nn.Module, str]:
    """
    DINOv2 모델 싱글톤을 반환.

    최초 호출 시 torch.hub.load()로 모델을 로드하고 eval() 모드로 전환합니다.

    Returns:
        (model, device) 튜플.
    """
    global _model, _device

    if _model is not None:
        return _model, _device

    settings = get_settings()
    model_name = settings.image.dino_model

    # 디바이스 결정
    if settings.image.dino_device:
        _device = settings.image.dino_device
    elif torch.cuda.is_available():
        _device = "cuda"
    else:
        _device = "cpu"

    logger.info(f"Loading DINOv2 model: {model_name} on {_device}")

    _model = torch.hub.load("facebookresearch/dinov2", model_name)
    _model = _model.to(_device)
    _model.eval()

    logger.info(f"DINOv2 model loaded: {model_name} ({_device})")
    return _model, _device


def get_transform() -> transforms.Compose:
    """
    DINOv2 입력용 이미지 전처리 파이프라인을 반환.

    Resize(256) -> CenterCrop(224) -> ToTensor -> Normalize(ImageNet mean/std)
    """
    global _transform

    if _transform is not None:
        return _transform

    _transform = transforms.Compose([
        transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.229, 0.224, 0.225],
        ),
    ])
    return _transform


def compute_embedding(file_path: str) -> Optional[list[float]]:
    """
    이미지 파일에서 DINOv2 임베딩 벡터를 생성.

    Args:
        file_path: 이미지 파일 절대 경로.

    Returns:
        임베딩 벡터 (float 리스트). 실패 시 None.
    """
    try:
        model, device = get_dino_model()
        transform = get_transform()

        with Image.open(file_path) as img:
            img_rgb = img.convert("RGB")
            tensor = transform(img_rgb).unsqueeze(0).to(device)

        with torch.no_grad():
            embedding = model(tensor)

        return embedding.squeeze(0).cpu().tolist()

    except Exception as e:
        logger.error(f"DINOv2 embedding failed for {file_path}: {e}")
        return None


def compute_embedding_batch(
    file_paths: list[str], batch_size: int = 16
) -> dict[str, list[float]]:
    """
    여러 이미지의 DINOv2 임베딩을 배치로 생성.

    Args:
        file_paths: 이미지 파일 경로 리스트.
        batch_size: 배치 크기.

    Returns:
        {file_path: embedding_vector} 딕셔너리. 실패한 파일은 제외.
    """
    results = {}
    model, device = get_dino_model()
    transform = get_transform()

    for i in range(0, len(file_paths), batch_size):
        batch_paths = file_paths[i:i + batch_size]
        tensors = []
        valid_paths = []

        for path in batch_paths:
            try:
                with Image.open(path) as img:
                    img_rgb = img.convert("RGB")
                    tensors.append(transform(img_rgb))
                    valid_paths.append(path)
            except Exception as e:
                logger.warning(f"Skipping {path} in batch: {e}")

        if not tensors:
            continue

        batch_tensor = torch.stack(tensors).to(device)

        with torch.no_grad():
            embeddings = model(batch_tensor)

        for path, emb in zip(valid_paths, embeddings):
            results[path] = emb.cpu().tolist()

    return results
