"""
이미지 클러스터링 모듈.

DINOv2 임베딩 벡터에 대해 Agglomerative Clustering을 수행하여
시각적으로 유사한 이미지를 그룹으로 분류합니다.
"""

import logging
from typing import Optional

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity

from config.settings import get_settings

logger = logging.getLogger(__name__)


def cluster_images(
    embeddings: dict[str, list[float]],
    distance_threshold: Optional[float] = None,
) -> dict[int, list[str]]:
    """
    DINOv2 임베딩 기반으로 이미지를 클러스터링.

    Args:
        embeddings: {image_name: embedding_vector} 딕셔너리.
        distance_threshold: 클러스터 거리 임계값.
            None이면 1 - near_duplicate_threshold 사용.

    Returns:
        {cluster_id: [image_name1, image_name2, ...], ...}
        단독 이미지(그룹 크기 1)는 제외합니다.
    """
    if len(embeddings) < 2:
        return {}

    settings = get_settings()
    if distance_threshold is None:
        distance_threshold = 1.0 - settings.image.near_duplicate_threshold

    names = list(embeddings.keys())
    matrix = np.array([embeddings[n] for n in names])

    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = clustering.fit_predict(matrix)

    groups: dict[int, list[str]] = {}
    for name, label in zip(names, labels):
        label_int = int(label)
        if label_int not in groups:
            groups[label_int] = []
        groups[label_int].append(name)

    # 단독 이미지 제외, 그룹 ID를 0부터 재번호
    multi_groups = {i: v for i, (_, v) in enumerate(
        (k, v) for k, v in sorted(groups.items()) if len(v) >= 2
    )}

    logger.info(
        f"Clustered {len(names)} images into {len(multi_groups)} groups "
        f"(threshold={distance_threshold:.3f})"
    )
    return multi_groups


def find_duplicates(
    embeddings: dict[str, list[float]],
    threshold: Optional[float] = None,
) -> list[list[str]]:
    """
    코사인 유사도가 임계값 이상인 이미지 쌍을 중복으로 식별.

    Args:
        embeddings: {image_name: embedding_vector} 딕셔너리.
        threshold: 중복 판별 임계값. None이면 Settings의 similarity_threshold 사용.

    Returns:
        중복 그룹 리스트. [[image1, image2], [image3, image4, image5], ...]
    """
    if len(embeddings) < 2:
        return []

    settings = get_settings()
    if threshold is None:
        threshold = settings.image.similarity_threshold

    names = list(embeddings.keys())
    matrix = np.array([embeddings[n] for n in names])

    sim_matrix = cosine_similarity(matrix)

    # Union-Find로 중복 그룹 생성
    parent = list(range(len(names)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if sim_matrix[i][j] >= threshold:
                union(i, j)

    # 그룹 수집
    groups_map: dict[int, list[str]] = {}
    for i, name in enumerate(names):
        root = find(i)
        if root not in groups_map:
            groups_map[root] = []
        groups_map[root].append(name)

    # 2개 이상인 그룹만 반환
    duplicates = [group for group in groups_map.values() if len(group) >= 2]

    if duplicates:
        logger.info(
            f"Found {len(duplicates)} duplicate group(s) "
            f"(threshold={threshold:.3f})"
        )

    return duplicates
