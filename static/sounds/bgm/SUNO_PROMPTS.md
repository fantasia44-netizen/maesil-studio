# Suno BGM 프롬프트 레퍼런스
> 각 분위기 폴더에 MP3 파일 5개씩 저장 (총 50곡)
> 파일명 예: `energetic_01.mp3`, `energetic_02.mp3` ...

---

## 폴더 구조
```
static/sounds/bgm/
├── energetic/        # 역동적·트렌디
├── emotional/        # 감성적·따뜻한
├── upbeat_pop/       # 경쾌한 팝
├── luxury/           # 고급스러운
├── playful/          # 귀엽고 재미있는
├── dramatic/         # 드라마틱·임팩트
├── calm_ambient/     # 차분한 앰비언트
├── trendy_hiphop/    # 트렌디 힙합
├── inspiring/        # 영감·동기부여
└── korean_vibe/      # 한국 감성
```

---

## 스타일 → 분위기 매핑
| 이미지 스타일 | 우선 분위기 |
|---|---|
| 실사 감성 (realistic_banner) | emotional → inspiring → luxury |
| 웹툰 (webtoon) | playful → upbeat_pop → trendy_hiphop |
| 지브리 (ghibli) | calm_ambient → emotional → korean_vibe |
| 모던 플랫 (flat_modern) | trendy_hiphop → energetic → upbeat_pop |
| 픽사/디즈니 (disney) | playful → upbeat_pop → inspiring |

---

## Suno 프롬프트 (각 폴더당 5회 생성)

### energetic/ — 에너지틱
```
upbeat energetic pop electronic, driving synth bass, punchy drums, motivational vibe, no lyrics, instrumental, 120-128 BPM, Korean commercial ad style, 30 seconds
```
변형 키워드: `high energy dance pop` / `EDM drop style` / `gym workout beat` / `summer festival vibes` / `power synth anthem`

---

### emotional/ — 감성적
```
warm emotional acoustic pop, soft piano melody, gentle strings, heartfelt mood, no lyrics, instrumental, 80-90 BPM, cinematic warmth, 30 seconds
```
변형 키워드: `rainy day piano` / `mother's love soundtrack` / `healing ballad instrumental` / `soft indie folk` / `nostalgic warmth`

---

### upbeat_pop/ — 경쾌한 팝
```
bright cheerful K-pop style, light guitar strum, happy whistling, summer vibes, no lyrics, instrumental, 100-110 BPM, fresh and fun, 30 seconds
```
변형 키워드: `morning coffee vibes` / `spring walk melody` / `happy ukulele pop` / `bright commercial jingle` / `sunshine pop`

---

### luxury/ — 고급스러운
```
elegant cinematic luxury, soft jazz piano, subtle orchestral sweep, sophisticated mood, no lyrics, instrumental, 70-80 BPM, premium brand feel, 30 seconds
```
변형 키워드: `5-star hotel lobby jazz` / `Paris fashion week ambience` / `high-end perfume ad` / `luxury yacht evening` / `refined minimal piano`

---

### playful/ — 귀엽고 재미있는
```
cute playful cartoon style, xylophone melody, bouncy ukulele, fun quirky sound effects, no lyrics, instrumental, 115-125 BPM, light and whimsical, 30 seconds
```
변형 키워드: `children's toy commercial` / `cute pet video BGM` / `kawaii pop instrumental` / `bouncy pixel game music` / `friendly cartoon theme`

---

### dramatic/ — 드라마틱
```
dramatic cinematic tension, pulsing low synth, building percussion, suspenseful mood, no lyrics, instrumental, 90-100 BPM, problem-aware tone, 30 seconds
```
변형 키워드: `thriller hook intro` / `mystery reveal music` / `intense problem statement` / `dark cinematic pulse` / `suspense buildup`

---

### calm_ambient/ — 차분한 앰비언트
```
calm lo-fi ambient, soft pad chords, gentle nature sounds, relaxing meditation vibe, no lyrics, instrumental, 60-70 BPM, peaceful and serene, 30 seconds
```
변형 키워드: `forest morning ambience` / `sleep meditation music` / `yoga session background` / `zen water sounds` / `breathing space ambient`

---

### trendy_hiphop/ — 트렌디 힙합
```
trendy lo-fi hip hop beat, warm vinyl crackle, chill boom-bap, modern Korean street vibe, no lyrics, instrumental, 85-95 BPM, cool and stylish, 30 seconds
```
변형 키워드: `Seoul street style beat` / `K-beauty vlog BGM` / `chill study hip hop` / `sneaker culture beat` / `urban chill trap`

---

### inspiring/ — 영감·동기부여
```
inspiring uplifting corporate, light piano arpeggios, rising strings, motivational crescendo, no lyrics, instrumental, 95-105 BPM, forward momentum, 30 seconds
```
변형 키워드: `startup success anthem` / `achievement moment music` / `TED talk background` / `dawn of new journey` / `breakthrough moment`

---

### korean_vibe/ — 한국 감성
```
modern K-indie acoustic, soft gayageum-inspired melody, gentle acoustic guitar, nostalgic Korean sentiment, no lyrics, instrumental, 80-90 BPM, warm and familiar, 30 seconds
```
변형 키워드: `한옥 뜰 감성` / `K-drama OST style` / `Korean countryside warmth` / `Hanok village ambient` / `modern K-folk instrumental`

---

## 사용 팁
- Suno에서 **Custom Mode** 사용 → Style of Music에 프롬프트 입력
- **Instrumental** 체크 필수 (가사 없음)
- 생성 후 30초짜리 클립 선택 또는 편집
- 저작권: Suno Pro/Premier 플랜에서 생성한 곡은 상업적 사용 가능
