"""이 앱의 버전.

개발 중에는 아래 기본값이다. 릴리스 빌드에서는 CI(.github/workflows/release.yml)가
태그(v0.1.4 -> 0.1.4)로 이 파일을 덮어쓴 뒤 PyInstaller로 굽는다. 그래서
업데이트 확인이 "지금 몇 버전인가"를 알 수 있다.
"""

__version__ = "0.0.0-dev"
