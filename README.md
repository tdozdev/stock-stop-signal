# stock-stop-signal (SSS)

KRX 종목에 대해 KOSPI 대비 초과 하락 기반 추적 손절 신호를 계산하고 텔레그램으로 알림을 보내는 봇입니다.

## Repository Structure

```text
stock-stop-signal/
  apps/
    bot/         # 기존 SSS 봇 코드
    api/         # FastAPI 스켈레톤
    admin-web/   # Next.js 스켈레톤
  infra/
    docker-compose.yml
  data/
  README.md
```

## UX 정책
- 기본 동작: 손절 트리거가 없으면 메시지를 보내지 않습니다.
- 선택 기능: `/daily on` 사용 시, 트리거가 없는 날에도 일일 리포트를 보냅니다.
- 상태 확인: `/status` 제공.
- 알림/리포트는 **KRX 개장일(거래일)** 에만 발송합니다. (비거래일은 배치 스킵)

## 전략 정의 (종가 기준 추적 손절)
`relative_drop_pct = ((peak_price - current_close) / peak_price * 100) - ((kospi_at_peak - current_kospi_close) / kospi_at_peak * 100)`

`relative_drop_pct >= stop_loss_pct` 이면 손절 신호로 판단합니다.

- `peak`는 `buy_date` 이후 종가 최고값입니다.
- 배치에서 개장일 아침(08:10 KST)에 **직전 거래일 기준 종가(effective_date)** 로 peak/손절을 계산합니다.

## Bot 실행

### 로컬
```bash
cd apps/bot
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp ../../.env.example ../../.env
# .env에 TELEGRAM_BOT_TOKEN 설정
python -m sss.app
```

### Docker Compose
```bash
cp .env.example .env
# .env에 TELEGRAM_BOT_TOKEN 설정
docker compose -f infra/docker-compose.yml up -d --build
```

## 테스트
```bash
cd apps/bot
pip install -e .[dev]
pytest
```

## API Skeleton
- 위치: `apps/api`
- 실행 예시:
```bash
cd apps/api
pip install -e .
uvicorn sss_api.main:app --reload
```

## Admin Web Skeleton
- 위치: `apps/admin-web`
- 로컬 실행 예시:
```bash
cd apps/admin-web
npm i
npm run dev
```
- Vercel 배포 시 Root Directory를 `apps/admin-web`로 지정하세요.
