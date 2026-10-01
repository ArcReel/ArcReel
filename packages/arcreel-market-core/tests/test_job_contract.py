from arcreel_market_core.job_contract import (
    ProviderJobStatus,
    ProviderResponseStage,
    ResumeExpiredError,
)
from arcreel_market_core.video_backend_contract import (
    ProviderJobStatus as VideoProviderJobStatus,
)
from arcreel_market_core.video_backend_contract import (
    ProviderResponseStage as VideoProviderResponseStage,
)
from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError as VideoResumeExpiredError,
)


def test_video_contract_reexports_media_neutral_job_contract_names() -> None:
    assert VideoProviderJobStatus is ProviderJobStatus
    assert VideoProviderResponseStage is ProviderResponseStage
    assert VideoResumeExpiredError is ResumeExpiredError
