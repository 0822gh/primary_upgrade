# analyzer/services/review_sentence_generator.py
from __future__ import annotations
from typing import Dict, List

from .review_sentence_templates import LABEL_REVIEW_TEXT


def generate_review_sentences(
    policy_labels: List[str],
    perm_labels: List[str],
    missing_in_policy: List[str],
    extra_in_policy: List[str],
    label_to_perms: Dict[str, List[str]] | None = None,
    max_evidence_per_label: int = 2,
) -> List[Dict]:
    """
    반환 형태:
      [
        {"label": "MED", "tag": "누락", "sentence": "...", "evidence": ["android.permission..."]},
        ...
      ]
    """
    label_to_perms = label_to_perms or {}

    def status_of(label: str) -> str:
        if label in (missing_in_policy or []):
            return "누락"
        if label in (extra_in_policy or []):
            return "과기재"
        return "일치"

    # 표시할 라벨 후보: (정책 라벨 ∪ 퍼미션 라벨)
    merged: List[str] = []
    for l in (policy_labels or []) + (perm_labels or []):
        if l and l not in merged:
            merged.append(l)

    out: List[Dict] = []
    for label in merged:
        tag = status_of(label)

        # 1) 라벨별 기본 설명(템플릿)만 문장에 사용
        base = LABEL_REVIEW_TEXT.get(
            label,
            f"{label} 관련 처리가 포함될 가능성이 있습니다. 관련 고지 여부를 점검하세요."
        )

        # 2) 근거 퍼미션은 sentence에 넣지 않고 evidence로만 내려줌
        evid_list = (label_to_perms.get(label) or [])
        evid_show = evid_list[:max_evidence_per_label]

        # 3) tag별 권고 문구만 뒤에 붙임 (원하면 이것도 더 짧게 줄일 수 있음)
        sentence = base
        if tag == "누락":
            sentence += " 문서에 관련 고지가 누락되었을 가능성이 있어 확인/보완을 권장합니다."
        elif tag == "과기재":
            sentence += " 문서에는 기재되어 있으나 구현 근거가 약할 수 있어 실제 구현/SDK 사용 여부 확인이 필요합니다."
        else:
            sentence += " 문서와 구현 가능성이 대체로 정합해 보이나 실제 수집·전송 여부는 확인이 필요합니다."

        out.append({
            "label": label,
            "tag": tag,              # UI 배지로 출력
            "sentence": sentence,    # 이제 [누락], (근거 권한 예: ...) 없음
            "evidence": evid_show,   # UI에서만 근거칩으로 출력
        })

    return out
