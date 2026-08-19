from fastapi.testclient import TestClient

from app.main import app


def test_app_starts_and_serves_health_check_with_scheduler_lifespan():
    """lifespan이 정상적으로 기동/응답/종료되는지 확인한다. 테스트 환경에는
    ENABLE_SYNC_SCHEDULER가 설정돼 있지 않으므로 실제로는 스케줄러 태스크가
    생성되지 않는 분기(task=None)를 타지만, 이 테스트의 목적은 그 분기
    여부와 무관하게 lifespan 컨텍스트 매니저 자체가 깨끗하게 열리고
    닫히는지 확인하는 것이다 — task가 있든 없든(있으면 run_scheduler_loop가
    다음 KST 04:00까지 asyncio.sleep으로 대기만 하다가 with 블록을 빠져나올
    때 task.cancel()로 즉시 정리된다) 둘 다 유효하게 테스트되는 경로다."""
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200


def test_scheduler_enabled_flag_parses_truthy_and_falsy_values():
    """_SCHEDULER_ENABLED는 모듈 임포트 시 os.getenv 한 번으로 평가되므로,
    런타임에 monkeypatch.setenv로 바꿔도 이미 만들어진 app에는 반영되지
    않는다 — 대신 그 파싱 로직을 뽑아둔 _env_flag_enabled를 직접 검증한다."""
    from app.main import _env_flag_enabled

    for value in ("1", "true", "True", "TRUE"):
        assert _env_flag_enabled(value) is True

    for value in ("0", "false", "", "yes", None):
        assert _env_flag_enabled(value) is False
