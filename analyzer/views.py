from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.utils import timezone
import os
import tempfile
import uuid
import time

from django.conf import settings

from .models import APKAnalysis
from .apk_analyzer import APKAnalyzer

# 코랩 모델 추론 함수
from .services.privacy_infer_hf import predict_policy_text
from .services.perm_label_map import map_permissions_to_labels, sources_loaded

# ✅ 1차 업그레이드: 라벨 기반 검토 문장 생성기
from .services.review_sentence_generator import generate_review_sentences


def index(request):
    """메인 페이지 - APK 업로드 폼"""
    return render(request, 'analyzer/index.html')


def _safe_remove_file(abs_path: str, tries: int = 10, delay: float = 0.2) -> None:
    """
    Windows에서 파일이 잠깐 잠길 수 있어(Defender/라이브러리 핸들),
    os.remove를 재시도해서 WinError 32를 완화.
    """
    if not abs_path:
        return

    for _ in range(tries):
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
            return
        except PermissionError:
            time.sleep(delay)
        except OSError:
            time.sleep(delay)


def upload_apk(request):
    """APK 파일 업로드 및 분석 시작"""
    if request.method == 'POST':
        apk_file = request.FILES.get('apk_file')
        policy_text = request.POST.get('policy_text', '').strip()

        print("policy_text length:", len(policy_text))
        print("policy_text head:", policy_text[:80])

        if not apk_file:
            messages.error(request, 'APK 파일을 선택해주세요.')
            return redirect('analyzer:index')

        # 파일 확장자 검증
        if not apk_file.name.lower().endswith('.apk'):
            messages.error(request, 'APK 파일만 업로드 가능합니다.')
            return redirect('analyzer:index')

        # 파일 크기 검증 (50MB 제한)
        if apk_file.size > 50 * 1024 * 1024:
            messages.error(request, '파일 크기는 50MB를 초과할 수 없습니다.')
            return redirect('analyzer:index')

        analysis_obj = None
        full_path = None  # 절대경로
        rel_temp_path = None  # MEDIA_ROOT 기준 상대경로(temp/...)

        try:
            # APKAnalysis 객체 생성
            analysis_obj = APKAnalysis.objects.create(
                file_name=apk_file.name,
                file_size=apk_file.size,
                status='pending'
            )

            # 요청마다 고유한 파일명으로 저장 (충돌 방지)
            uid = uuid.uuid4().hex
            base_name = os.path.basename(apk_file.name)
            unique_name = f"{uid}_{base_name}"

            # MEDIA_ROOT/temp/ 아래에 직접 저장
            rel_temp_path = os.path.join("temp", unique_name)
            full_path = os.path.join(settings.MEDIA_ROOT, rel_temp_path)

            # temp 폴더 생성 보장
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # 파일 저장 (chunks 사용 + with로 핸들 확실히 닫힘)
            with open(full_path, "wb") as out:
                for chunk in apk_file.chunks():
                    out.write(chunk)

            # 분석 시작
            analysis_obj.status = 'analyzing'
            analysis_obj.save()

            # --- 정책 텍스트 라벨 예측 (코랩 모델) ---
            policy_labels = []
            policy_polarity = None
            policy_scores = {}

            if policy_text:
                try:
                    pred = predict_policy_text(
                        policy_text,
                        split_long_doc=True,
                        aggregate="max",
                        thr_pol=0.5
                    )
                    policy_labels = pred.get('pred_labels', [])
                    policy_polarity = pred.get('pred_polarity')
                    policy_scores = pred.get('probs_multi', {})

                except Exception:
                    # 모델 추론 실패 시 마킹만 하고 계속 진행
                    policy_labels = ['predict_fail']
                    policy_polarity = None
                    policy_scores = {}

            analysis_obj.policy_labels = policy_labels

            # 1차 업그레이드 필드 초기화(있으면)
            if hasattr(analysis_obj, 'policy_sentence_labels'):
                analysis_obj.policy_sentence_labels = []

            # APK 분석 수행 (CSV 내보내기 포함)
            analyzer = APKAnalyzer(full_path)

            # CSV 파일을 media/analysis_csv/<id>/ 디렉토리에 저장
            csv_output_dir = os.path.join(settings.MEDIA_ROOT, 'analysis_csv', str(analysis_obj.id))
            os.makedirs(csv_output_dir, exist_ok=True)

            result = analyzer.analyze_with_csv_export(csv_output_dir)

            if result.get('status') == 'completed':
                # 분석 결과 저장
                basic_info = result.get('basic_info', {})
                analysis_obj.package_name = basic_info.get('package_name', '')
                analysis_obj.version_name = basic_info.get('version_name', '')
                analysis_obj.version_code = basic_info.get('version_code', '')
                analysis_obj.min_sdk = basic_info.get('min_sdk', '')
                analysis_obj.target_sdk = basic_info.get('target_sdk', '')

                analysis_obj.permissions = result.get('permissions', [])
                analysis_obj.activities = result.get('activities', [])
                analysis_obj.services = result.get('services', [])
                analysis_obj.receivers = result.get('receivers', [])
                analysis_obj.api_calls = result.get('api_calls', [])
                analysis_obj.security_analysis = result.get('security_analysis', {})

                # CSV 파일 정보 저장 (파일명만 추출)
                csv_files = result.get('csv_files', [])
                csv_filenames = [os.path.basename(f) for f in csv_files]
                analysis_obj.csv_files = csv_filenames
                analysis_obj.csv_output_dir = result.get('csv_output_dir', csv_output_dir)

                analysis_obj.status = 'completed'
                analysis_obj.analysis_time = timezone.now()

            else:
                analysis_obj.status = 'failed'
                analysis_obj.error_message = result.get('error', '알 수 없는 오류가 발생했습니다.')

            analysis_obj.save()

            return redirect('analyzer:analysis_detail', analysis_id=analysis_obj.id)

        except Exception as e:
            # DB 기록이 있으면 failed로 남김
            if analysis_obj is not None:
                analysis_obj.status = 'failed'
                analysis_obj.error_message = str(e)
                analysis_obj.save()

            messages.error(request, f'분석 중 오류가 발생했습니다: {str(e)}')
            return redirect('analyzer:index')

        finally:
            # 임시 APK 파일 삭제는 항상 시도 (Windows 잠금 대비 재시도)
            if full_path:
                _safe_remove_file(full_path)

    return redirect('analyzer:index')


def analysis_list(request):
    """분석 결과 목록 페이지"""
    analyses = APKAnalysis.objects.all()
    return render(request, 'analyzer/analysis_list.html', {'analyses': analyses})


def analysis_detail(request, analysis_id):
    """분석 결과 상세 페이지 + 라벨 불일치 리포트"""
    analysis_obj = get_object_or_404(APKAnalysis, id=analysis_id)

    # 처리방침 예측 라벨 set
    policy_labels = set(analysis_obj.policy_labels or [])

    # 퍼미션 -> 라벨 매핑
    perms = analysis_obj.permissions or []
    perm_labels, label_by_perm, unknown_perms = map_permissions_to_labels(perms)

    # 불일치 계산
    missing_in_policy = sorted(perm_labels - policy_labels)  # 앱은 사용하나 처리방침에 미기재
    extra_in_policy   = sorted(policy_labels - perm_labels)  # 처리방침엔 있으나 퍼미션 근거 약함

    # 라벨별 근거 퍼미션 역인덱스
    label_to_perms = {}
    for p, labs in label_by_perm.items():
        for lb in labs:
            label_to_perms.setdefault(lb, []).append(p)
    for k in list(label_to_perms.keys()):
        label_to_perms[k] = sorted(label_to_perms[k])

    # 간단 메시지
    compliance_messages = []
    if missing_in_policy:
        compliance_messages.append(f"처리방침에 누락된 라벨: {', '.join(missing_in_policy)}")
    if extra_in_policy:
        compliance_messages.append(f"앱 권한으로는 확인되지 않는 라벨: {', '.join(extra_in_policy)}")
    if unknown_perms:
        compliance_messages.append(f"매핑에 없는 퍼미션(검토 필요): {', '.join(sorted(unknown_perms))}")

    # ✅ 1차 업그레이드: 라벨 기반 “검토 문장” 생성
    review_sentences = generate_review_sentences(
        policy_labels=sorted(policy_labels),
        perm_labels=sorted(perm_labels),
        missing_in_policy=missing_in_policy,
        extra_in_policy=extra_in_policy,
        label_to_perms=label_to_perms,
        max_evidence_per_label=2,
    )

    # ✅ (선택) DB에 저장: 비어있을 때만 채우기
    # 이미 저장된 값이 있으면 그대로 두고, 없으면 생성값을 저장
    if hasattr(analysis_obj, "policy_sentence_labels"):
        if not analysis_obj.policy_sentence_labels:
            analysis_obj.policy_sentence_labels = review_sentences
            analysis_obj.save(update_fields=["policy_sentence_labels"])

    return render(request, 'analyzer/analysis_detail.html', {
        'analysis': analysis_obj,

        # ▼ 템플릿에서 쓸 비교 결과들
        'policy_labels_set': sorted(policy_labels),
        'perm_labels_set':   sorted(perm_labels),
        'missing_in_policy': missing_in_policy,
        'extra_in_policy':   extra_in_policy,
        'unknown_perms':     sorted(unknown_perms),
        'label_to_perms':    label_to_perms,
        'compliance_messages': compliance_messages,

        # ▼ 1차 업그레이드 결과
        'review_sentences': review_sentences,

        # 'sources_loaded':  sources_loaded(),  # 필요하면 어떤 파일을 읽었는지 확인용
    })


def analysis_status(request, analysis_id):
    """분석 상태 확인 API"""
    analysis_obj = get_object_or_404(APKAnalysis, id=analysis_id)
    return JsonResponse({
        'status': analysis_obj.status,
        'error_message': analysis_obj.error_message
    })
