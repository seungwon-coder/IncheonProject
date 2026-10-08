# 모델 파일 대조 결과

V13.29 폴더의 vision1_best.pt, vision2_best.pt, vision3_best.pt, vision4_best.pt는 SHA256이 모두 같으며 사용자가 실제 사용 모델로 지정한 train-v15-best/weights/best.pt와도 동일합니다.

SHA256: `1e26e2513a894c812bbf188bbfd827bc9304640054e87c708b6d75b832ee4576`

원본 MODEL_APPLIED.txt의 기록: 사용자 제공 best(2).pt, detect, 클래스 car body / car lamp_A / car lamp_R / car seat. 이 모델 자체는 시트 색상 종류를 구분하지 않으며 V2·V4의 색상 처리는 Python/OpenCV가 보완합니다. 최신 V3의 시트 유무는 ROI 픽셀 비율을 사용합니다.

모델의 동일성은 해시로 확인했습니다. 가중치 추론 성능과 현장 적용 결과를 이번 작업에서 재검증한 것은 아닙니다. 모델 바이너리는 이번 공개본에서 제외합니다. 과거 models/README_KO.txt의 V1/V2 역할이 뒤바뀐 안내는 그대로 복사하지 않았습니다.
