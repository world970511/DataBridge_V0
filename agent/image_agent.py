"""
이미지 검색/분석 에이전트 모듈 — 시각적 유사도 검색, 클러스터링, 중복 탐지를 수행.

사용자의 이미지 관련 질의를 처리하는 에이전트입니다. 다음 기능을 제공합니다:

1. **유사 이미지 검색**: DINOv2 임베딩 기반 코사인 유사도로 시각적으로 유사한 이미지 검색
2. **이미지 그룹핑**: Agglomerative Clustering으로 시각적 유사 이미지를 그룹 분류
3. **중복 이미지 탐지**: 코사인 유사도 임계값 기반 중복 이미지 쌍 식별
4. **이미지 목록 조회**: 카탈로그에 등록된 전체 이미지 메타데이터 조회
5. **이미지 상세 정보**: 특정 이미지의 EXIF 메타데이터 반환

의존 모듈:
    - agent._llm: generate() — LLM 호출 (그룹 설명 생성 등)
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.search_images: search_by_name(), search_all_images(), get_image_names()
    - rag.image.image_store: get_all_embeddings() — 클러스터링/중복 탐지용
    - rag.image.clustering: cluster_images(), find_duplicates() — 분석 알고리즘
    - catalog.catalog: get_image_by_name(), list_images() — 카탈로그 조회

사용 예시:
    from agent.image_agent import process
    result = process("sunset.jpg와 비슷한 이미지 찾아줘", sub_intent="search")
    print(result["answer"])    # "유사 이미지 3건을 찾았습니다: ..."
    print(result["images"])    # [{"image_name": "beach.jpg", "similarity": 0.92}, ...]
"""

import logging

from agent._llm import generate
from agent._audit import log_action
from agent.tools.search_images import (
    search_by_name,
    search_all_images,
    get_image_names,
    format_image_results,
)

logger = logging.getLogger(__name__)

# 이미지 분석 결과를 LLM으로 설명 생성할 때 사용하는 시스템 프롬프트.
_IMAGE_SYSTEM_PROMPT = """You are an image analysis expert.
Describe the image search/analysis results to the user in a clear and helpful way.

Rules:
- Summarize the results concisely in Korean
- Mention image names and similarity scores when available
- For grouping results, describe what the groups might represent
- For duplicate detection, clearly indicate which images are likely duplicates
"""


def process(question: str, sub_intent: str = "search") -> dict:
    """
    이미지 관련 질의를 sub_intent에 따라 적절한 처리 함수로 라우팅.

    Args:
        question: 사용자의 자연어 질의.
        sub_intent: 세부 의도.
            - "search": 유사 이미지 검색
            - "group": 이미지 클러스터링/그룹핑
            - "duplicates": 중복 이미지 탐지
            - "list": 전체 이미지 목록 조회
            - "info": 특정 이미지 상세 정보

    Returns:
        처리 결과 딕셔너리:
        {
            "success": bool,
            "answer": str,           — 사용자에게 표시할 자연어 응답
            "images": list[dict],    — 이미지 결과 리스트
            "groups": dict | None,   — 클러스터링 그룹 (group 의도 시)
            "duplicates": list | None, — 중복 그룹 (duplicates 의도 시)
            "agent": "image",
        }
    """
    log_action(action_type="query", query_text=question, metadata={"sub_intent": sub_intent})

    handlers = {
        "search": _process_similar_search,
        "group": _process_image_grouping,
        "duplicates": _process_duplicate_detection,
        "list": _process_image_list,
        "info": _process_image_info,
    }

    handler = handlers.get(sub_intent, _process_similar_search)

    try:
        result = handler(question)
    except Exception as e:
        logger.exception(f"Image agent error (sub_intent={sub_intent})")
        result = {
            "success": False,
            "answer": f"이미지 처리 중 오류가 발생했습니다: {str(e)}",
            "images": [],
            "agent": "image",
        }

    log_action(
        action_type="image_answer",
        query_text=question,
        result_summary=result.get("answer", "")[:500],
        status="success" if result.get("success") else "failed",
        metadata={"sub_intent": sub_intent, "image_count": len(result.get("images", []))},
    )

    return result


def _find_image_name_in_question(question: str) -> str | None:
    """
    질의에 포함된 이미지 파일명을 카탈로그와 대조하여 추출.

    파일명이 긴 것부터 매칭하여 부분 매칭 오류를 방지합니다.
    확장자 포함/제거 양쪽 모두 시도합니다.

    Returns:
        매칭된 이미지 파일명 또는 None.
    """
    question_lower = question.lower()
    names = get_image_names()

    for name in sorted(names, key=len, reverse=True):
        if name.lower() in question_lower:
            return name
        base_name = name.rsplit(".", 1)[0] if "." in name else name
        if base_name.lower() in question_lower:
            return name

    return None


def _process_similar_search(question: str) -> dict:
    """
    질의에서 이미지명을 추출하고 시각적으로 유사한 이미지를 검색.

    이미지명이 질의에 없으면 안내 메시지를 반환합니다.
    """
    image_name = _find_image_name_in_question(question)

    if not image_name:
        # 이미지명 없이 일반적인 검색 요청 → 목록 안내
        all_images = get_image_names()
        if not all_images:
            return {
                "success": True,
                "answer": "등록된 이미지가 없습니다. 이미지를 업로드한 후 다시 시도해 주세요.",
                "images": [],
                "agent": "image",
            }

        names_preview = ", ".join(all_images[:10])
        suffix = f" 외 {len(all_images) - 10}건" if len(all_images) > 10 else ""
        return {
            "success": True,
            "answer": (
                f"검색할 이미지명을 지정해 주세요. "
                f"현재 등록된 이미지: {names_preview}{suffix}\n\n"
                f"예시: \"sunset.jpg와 비슷한 이미지 찾아줘\""
            ),
            "images": [],
            "agent": "image",
        }

    results = search_by_name(image_name, n_results=10)

    if not results:
        return {
            "success": True,
            "answer": f"'{image_name}'과 유사한 이미지를 찾지 못했습니다.",
            "images": [],
            "agent": "image",
        }

    answer = format_image_results(results)

    return {
        "success": True,
        "answer": answer,
        "images": results,
        "agent": "image",
    }


def _process_image_grouping(question: str) -> dict:
    """
    전체 이미지 임베딩에 대해 클러스터링을 수행하고 그룹 결과를 반환.

    LLM으로 각 그룹의 설명을 생성합니다.
    """
    from rag.image.image_store import get_all_embeddings
    from rag.image.clustering import cluster_images

    embeddings = get_all_embeddings()

    if len(embeddings) < 2:
        return {
            "success": True,
            "answer": "그룹핑하려면 최소 2개 이상의 이미지가 필요합니다. "
                      f"현재 등록된 이미지: {len(embeddings)}건",
            "images": [],
            "groups": {},
            "agent": "image",
        }

    groups = cluster_images(embeddings)

    if not groups:
        return {
            "success": True,
            "answer": "시각적으로 유사한 이미지 그룹을 찾지 못했습니다. "
                      "모든 이미지가 서로 다른 특성을 가지고 있습니다.",
            "images": [],
            "groups": {},
            "agent": "image",
        }

    # 그룹 결과를 텍스트로 포맷팅
    answer_parts = [f"이미지를 {len(groups)}개 그룹으로 분류했습니다:\n"]
    all_images = []

    for group_id, members in groups.items():
        answer_parts.append(f"**그룹 {group_id + 1}** ({len(members)}장): {', '.join(members)}")
        for name in members:
            all_images.append({"image_name": name, "group_id": group_id})

    answer = "\n".join(answer_parts)

    # LLM으로 그룹 설명 생성 (선택적)
    group_desc = _generate_group_description(groups)
    if group_desc:
        answer += f"\n\n{group_desc}"

    return {
        "success": True,
        "answer": answer,
        "images": all_images,
        "groups": {k: v for k, v in groups.items()},
        "agent": "image",
    }


def _process_duplicate_detection(question: str) -> dict:
    """
    전체 이미지 임베딩에서 중복 이미지 그룹을 탐지.

    정보 표시만 수행하며 삭제 기능은 제공하지 않습니다.
    """
    from rag.image.image_store import get_all_embeddings
    from rag.image.clustering import find_duplicates

    embeddings = get_all_embeddings()

    if len(embeddings) < 2:
        return {
            "success": True,
            "answer": "중복 탐지하려면 최소 2개 이상의 이미지가 필요합니다. "
                      f"현재 등록된 이미지: {len(embeddings)}건",
            "images": [],
            "duplicates": [],
            "agent": "image",
        }

    duplicates = find_duplicates(embeddings)

    if not duplicates:
        return {
            "success": True,
            "answer": "중복 이미지가 발견되지 않았습니다. 모든 이미지가 고유합니다.",
            "images": [],
            "duplicates": [],
            "agent": "image",
        }

    # 결과 포맷팅
    total_dup = sum(len(g) for g in duplicates)
    answer_parts = [f"중복 가능 이미지 {len(duplicates)}개 그룹 (총 {total_dup}장)을 발견했습니다:\n"]
    all_images = []

    for idx, group in enumerate(duplicates, 1):
        answer_parts.append(f"**중복 그룹 {idx}**: {', '.join(group)}")
        for name in group:
            all_images.append({"image_name": name, "duplicate_group": idx})

    answer_parts.append(
        "\n> 중복 여부를 확인하신 후, 데이터 관리 페이지에서 삭제 요청을 보내실 수 있습니다."
    )

    return {
        "success": True,
        "answer": "\n".join(answer_parts),
        "images": all_images,
        "duplicates": duplicates,
        "agent": "image",
    }


def _process_image_list(question: str) -> dict:
    """카탈로그에 등록된 전체 이미지 목록을 반환."""
    images = search_all_images()

    if not images:
        return {
            "success": True,
            "answer": "등록된 이미지가 없습니다.",
            "images": [],
            "agent": "image",
        }

    answer_parts = [f"등록된 이미지 {len(images)}건:\n"]
    for i, img in enumerate(images, 1):
        name = img.get("image_name", "unknown")
        file_type = img.get("file_type", "")
        size = img.get("file_size_bytes", 0)
        size_str = f"{size / 1024:.0f}KB" if size else ""
        camera = img.get("camera_model", "")

        line = f"{i}. **{name}**"
        details = []
        if file_type:
            details.append(file_type.upper())
        if size_str:
            details.append(size_str)
        if camera:
            details.append(f"카메라: {camera}")
        if details:
            line += f" ({', '.join(details)})"

        answer_parts.append(line)

    return {
        "success": True,
        "answer": "\n".join(answer_parts),
        "images": images,
        "agent": "image",
    }


def _process_image_info(question: str) -> dict:
    """질의에서 이미지명을 추출하고 EXIF 메타데이터를 반환."""
    from catalog.catalog import get_image_by_name

    image_name = _find_image_name_in_question(question)

    if not image_name:
        return {
            "success": True,
            "answer": "정보를 조회할 이미지명을 지정해 주세요.\n예시: \"sunset.jpg 정보 알려줘\"",
            "images": [],
            "agent": "image",
        }

    info = get_image_by_name(image_name)

    if not info:
        return {
            "success": True,
            "answer": f"'{image_name}' 이미지를 카탈로그에서 찾을 수 없습니다.",
            "images": [],
            "agent": "image",
        }

    # EXIF 정보를 읽기 쉬운 형태로 포맷팅
    answer_parts = [f"**{image_name}** 상세 정보:\n"]

    field_labels = [
        ("file_type", "파일 형식"),
        ("file_size_bytes", "파일 크기"),
        ("width", "가로"),
        ("height", "세로"),
        ("camera_make", "카메라 제조사"),
        ("camera_model", "카메라 모델"),
        ("lens_info", "렌즈"),
        ("focal_length", "초점 거리"),
        ("aperture", "조리개"),
        ("shutter_speed", "셔터 속도"),
        ("iso", "ISO"),
        ("date_taken", "촬영 일시"),
        ("gps_latitude", "GPS 위도"),
        ("gps_longitude", "GPS 경도"),
    ]

    for field, label in field_labels:
        value = info.get(field)
        if value is not None and value != "" and value != 0:
            if field == "file_size_bytes":
                if value >= 1024 * 1024:
                    value = f"{value / (1024 * 1024):.1f} MB"
                else:
                    value = f"{value / 1024:.0f} KB"
            elif field == "focal_length":
                value = f"{value}mm"
            elif field == "aperture":
                value = f"f/{value}"
            answer_parts.append(f"- **{label}**: {value}")

    return {
        "success": True,
        "answer": "\n".join(answer_parts),
        "images": [info],
        "agent": "image",
    }


def _generate_group_description(groups: dict[int, list[str]]) -> str:
    """
    LLM으로 클러스터링 그룹에 대한 설명을 생성.

    실패 시 빈 문자열을 반환합니다 (선택적 기능이므로 에러 무시).
    """
    try:
        group_info = "\n".join(
            f"Group {gid + 1}: {', '.join(members)}"
            for gid, members in groups.items()
        )
        prompt = (
            f"The following images have been grouped by visual similarity:\n\n"
            f"{group_info}\n\n"
            f"Based on the file names, briefly describe what each group might contain. "
            f"Answer in Korean, 1-2 sentences per group."
        )

        return generate(
            prompt=prompt,
            system=_IMAGE_SYSTEM_PROMPT,
            purpose="agent",
            temperature=0.3,
        )

    except Exception as e:
        logger.debug(f"Group description generation failed (non-critical): {e}")
        return ""
