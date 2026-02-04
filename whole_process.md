# 전체 처리 흐름 (Whole Process)

다이어그램은 아래 코드 블록 안에 있어 Markdown이 테이블로 해석하지 않고, 그대로 정렬되어 보입니다.

```
┌──────────────────────┐
│ 1) User uploads file  │  사용자가 파일 업로드
│    (PDF / TXT)        │  (PDF / TXT)
└─────────┬────────────┘
          │
          ▼
┌──────────────────────┐
│ Object Storage        │  객체 스토리지
│ (File is saved here)  │  (파일이 여기에 저장됨)
└─────────┬────────────┘
          │
          │ (Every 1 minute)  매 1분마다
          ▼
┌──────────────────────────────────────────┐
│ ADB "Poller" (a timer inside the DB)     │  ADB "폴러" (DB 내부 타이머)
│ - Looks into the bucket                  │  - 버킷을 조회
│ - Finds "new files not processed yet"    │  - "아직 처리되지 않은 새 파일" 탐지
│ - Creates a "job" record in a table      │  - 테이블에 "작업" 레코드 생성
└─────────┬────────────────────────────────┘
          │  creates  생성
          ▼
┌──────────────────────┐
│ Job Queue Table       │  작업 큐 테이블
│ status = PENDING      │  status = PENDING (대기 중)
│ file = xxx.pdf        │  file = xxx.pdf
└─────────┬────────────┘
          │
          │ (Every 30 seconds, one or more workers)  매 30초마다, 워커 1개 이상
          ▼
┌──────────────────────────────────────────┐
│ ADB "Worker" (another timer in the DB)   │  ADB "워커" (DB 내 또 다른 타이머)
│ - Takes 1 PENDING job                    │  - PENDING 작업 1건 선택
│ - Downloads the file from storage        │  - 스토리지에서 파일 다운로드
│ - Converts PDF→text (or reads text)      │  - PDF→텍스트 변환 (또는 텍스트 읽기)
│ - Splits text into chunks                │  - 텍스트를 청크로 분할
│ - Makes embeddings for each chunk        │  - 각 청크에 대해 임베딩 생성
│ - Saves results                          │  - 결과 저장
│ - Marks job DONE (or ERROR)              │  - 작업을 DONE(또는 ERROR)으로 표시
└─────────┬────────────────────────────────┘
          │  saves  저장
          ▼
┌──────────────────────┐
│ Chunk + Vector Table  │  청크 + 벡터 테이블
│ chunk text + embedding│  청크 텍스트 + 임베딩
└──────────────────────┘
```

---

## 역할 요약 (Summary)

| 역할 | 영어 | 한글 설명 |
|------|------|-----------|
| **User** | uploads the file | 파일을 업로드 |
| **Object Storage** | holds the file | 파일을 보관 |
| **ADB Poller** | periodically checks storage for new files and writes "to-do items" into the Job Queue table | 주기적으로 스토리지를 확인해 새 파일을 찾고, 작업 큐 테이블에 "할 일" 항목을 기록 |
| **ADB Worker** | picks up each to-do item, processes the file, and stores chunk + embeddings, then updates the job status | 각 할 일 항목을 가져와 파일을 처리하고, 청크와 임베딩을 저장한 뒤 작업 상태를 갱신 |
