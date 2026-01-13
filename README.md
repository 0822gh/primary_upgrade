APK Analyzer (Django) — CPU-only

APK 파일을 업로드하면 정적 분석(권한/컴포넌트/API 등)과 함께
처리방침 텍스트에서 개인정보 관련 라벨을 예측합니다.


1. Requirements

- Windows 10/11
- Python 3.12
- Microsoft Visual C++ 2015–2022 x64 재배포 패키지 (권장)


2. Setup (Windows PowerShell)

프로젝트 루트에서 실행합니다.

python -m venv venv
.\venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt


PyTorch (CPU 전용) 설치

Windows에서는 반드시 CPU 전용 wheel을 사용해야 합니다.

pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu


3. Model files (필수)

처리방침(privacy policy) 라벨 예측 모델 파일을
아래 경로에 위치시켜야 합니다.

apk_analyzer/out_ps_ovr/

필요한 파일 예시는 다음과 같습니다.

- best.ckpt 또는 state.pt
- labels.json
- thresholds.json (선택)

이 폴더는 .gitignore에 의해 GitHub에는 올라가지 않습니다.
로컬 환경에서만 준비해야 합니다.


4. Permission mapping files (필수)

퍼미션 → 라벨 매핑 파일을 아래 폴더에 둡니다.

mapping/

지원 형식:
- CSV
- XLSX

컬럼 예시:
Permission, Tag


5. Django settings 확인

apk_analyzer/settings.py 에서 모델 경로가 정확한지 확인합니다.

POLICY_HF_MODEL_DIR = BASE_DIR / "apk_analyzer" / "out_ps_ovr"


6. Run

python manage.py migrate
python manage.py runserver --noreload

브라우저에서 접속:
http://127.0.0.1:8000/


7. Notes

- 업로드된 APK 및 분석 결과는 media/ 아래에 저장됩니다.
- media/ 디렉터리는 GitHub에 올라가지 않습니다.
- XLSX 매핑 파일을 읽기 위해 openpyxl 이 필요합니다.
