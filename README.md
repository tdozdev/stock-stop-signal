# stock-stop-signal (SSS)

KRX 종목에 대해 KOSPI 대비 초과 하락 기반 추적 손절 신호를 계산하고 텔레그램으로 알림을 보내는 봇입니다.

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

## 명령어
- `/start`
- 손절 설정: `/s 10`, `/stop 10`, `/손절 10`
- Daily 리포트: `/daily on`, `/daily off`
- 상태 확인: `/status`
- 종목 추가: `/c 005930 70000 20250115`, `/c 005930 70000`
- 종목 삭제: `/d 005930`
- 종목 수정: `/u 005930 72000`, `/u 005930 72000 20250115`
- 전체 보기: `/r`
- 개별 보기: `/r 005930`

## 날짜 규칙
- 입력: `YYYYMMDD`
- 내부 저장: `YYYY-MM-DD`
- 미입력 시 오늘(KST)
- 형식/유효성 검증 수행

## 배치 스케줄
기본: 매일 08:10 KST

1. XKRX 거래일 캘린더 시드/리필 확인
2. 오늘이 비거래일이면 배치/발송 전체 스킵
3. 거래일이면 `effective_date=오늘 기준 직전 거래일` 계산
4. holdings 유니크 symbol 추출
5. `effective_date` 종가 수집 후 `price_cache` 저장
6. peak 갱신
7. relative_drop 계산
8. trigger 종목 `notifications` 기록 (`trading_date=effective_date`)
9. 사용자별 trigger 요약 1건 발송
10. `daily_report=1` 이고 trigger 없는 사용자에게 daily 리포트 발송

## 레이트리밋/중복 방지
- 발송 속도: 초당 25건 이하
- Telegram `429` 시 `retry_after` 대기 후 재시도
- 동일 날짜/사용자/종목/type 조합은 DB PK로 중복 방지
- 사용자별 요약 메시지(`summary`)는 하루 1건

## 실행
### 로컬
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
# .env에 TELEGRAM_BOT_TOKEN 설정
python -m sss.app
```

### Docker
```bash
cp .env.example .env
# .env에 TELEGRAM_BOT_TOKEN 설정
docker compose up -d --build
```

## 테스트
```bash
pip install -e .[dev]
pytest
```

## 합리적 가정
- 기본 market provider는 `pykrx`입니다.
- `SSS_MARKET_PROVIDER=krx_api` 설정 시 KRX 공식 API(전종목 일별 + KOSPI 시리즈 일별)로 전환할 수 있습니다.
- 입력된 `buy_date`가 거래일이 아니어도 저장은 허용하되, 해당 날짜 이후 거래 데이터로 peak 계산을 시도합니다.
- `TELEGRAM_BOT_TOKEN`이 없으면 앱은 대기 모드로 실행되어 프로세스는 유지되지만 텔레그램 송수신은 수행하지 않습니다.
- 거래일 판단은 `exchange_calendars`의 `XKRX`를 사용하며, `trading_calendar` 테이블에 거래일을 캐시합니다.
- 캘린더 시딩 범위 기본값은 **과거 365일 ~ 미래 730일** 입니다.

## KRX 공식 API 전환
`.env`에 아래 값을 넣고 재시작하면 됩니다.

```env
SSS_MARKET_PROVIDER=krx_api
SSS_KRX_API_BASE_URL=https://data-dbg.krx.co.kr
SSS_KRX_API_KEY=<auth key>
SSS_KRX_KOSPI_DAILY_PATH=/svc/apis/sto/stk_bydd_trd
SSS_KRX_KOSDAQ_DAILY_PATH=/svc/apis/sto/ksq_bydd_trd
SSS_KRX_ETF_DAILY_PATH=/svc/apis/etp/etf_bydd_trd
SSS_KRX_KOSPI_INDEX_DAILY_PATH=/svc/apis/idx/kospi_dd_trd
SSS_KRX_DATE_PARAM=basDd
SSS_KRX_TIMEOUT_SEC=10
```

- 응답은 `OutBlock_1` 배열을 기대합니다.
- 종목 종가는 `TDD_CLSPRC`, 종목코드는 `ISU_CD`, 종목명은 `ISU_NM`을 사용합니다.
- ETF 종가는 `SSS_KRX_ETF_DAILY_PATH`(기본: `/svc/apis/etp/etf_bydd_trd`) 승인 시 함께 수집됩니다.
- KOSPI 지수 종가는 `CLSPRC_IDX`를 사용합니다.
