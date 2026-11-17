import logging

logger = logging.getLogger(__name__)

# Decisions are made by class NAME, not by index, so a retrained model whose
# class order changes cannot silently turn an NG into a GOOD.
NO_RING = "NO_RING"
R1_GOOD = "R1_GOOD"
R1_NG = "R1_NG"
R2_GOOD = "R2_GOOD"
R2_NG = "R2_NG"

EXPECTED_CLASSES = frozenset({NO_RING, R1_GOOD, R1_NG, R2_GOOD, R2_NG})


def normalize_class_name(name) -> str:
    return str(name).strip().upper() if name is not None else ""


class Logic:
    def __init__(self, min_confidence: float = 0.85):
        self.state = 1
        self.min_confidence = min_confidence

    def evaluate(self, info: tuple | dict, round_index: int) -> tuple[bool, str]:
        if not info:
            return False, "DETECTION ERROR"

        detection = info[0] if isinstance(info, (tuple, list)) else info
        if not isinstance(detection, dict):
            return False, "DETECTION ERROR"

        class_name = normalize_class_name(detection.get("class_name"))
        try:
            confidence = float(detection.get("confidence", 0.0))
        except (TypeError, ValueError):
            return False, "DETECTION ERROR"

        if class_name not in EXPECTED_CLASSES:
            logger.warning("Unknown class_name=%r (class_id=%s)", detection.get("class_name"), detection.get("class_id"))
            return False, "DETECTION ERROR"

        if confidence < self.min_confidence:
            logger.warning("Low confidence: %.2f < %.2f (class=%s)", confidence, self.min_confidence, class_name)
            return False, "LOW CONFIDENCE"

        if round_index == 0:
            if class_name == NO_RING:
                return True, "PASS (EMPTY)"
            elif class_name == R1_GOOD:
                return True, "PASS"
            elif class_name == R1_NG:
                return False, "FAIL"
            else:
                return False, "DETECTION ERROR"

        elif round_index == 1:
            if class_name == NO_RING:
                return True, "PASS (EMPTY)"
            elif class_name == R2_GOOD:
                return True, "PASS"
            elif class_name == R1_GOOD:
                # Lot-end: this slot has no R2 ring (e.g. 5 pcs -> R1 x3, R2 x2).
                return True, "PASS (R1 ONLY)"
            elif class_name in {R2_NG, R1_NG}:
                return False, "FAIL"
            else:
                return False, "DETECTION ERROR"

        return False, "INVALID_ROUND"

    def evaluate_all_slots(self, slots_info: list, round_index: int) -> tuple[bool, list[tuple[bool, str]]]:

        slot_results = [self.evaluate(info, round_index) for info in slots_info]
        overall_pass = all(status for status, _ in slot_results)
        return overall_pass, slot_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logic = Logic(min_confidence=0.85)

    # จำลองสถานการณ์ท้ายวัน: โหลด 3 ช่องพร้อมกัน แต่เหลือชิ้นงานแค่ 2 ชิ้น (ช่องที่ 3 เป็น NO_RING)
    slot_1 = ({"class_id": 3, "class_name": "R2_GOOD", "confidence": 0.96}, (102, 0, 115, 320))
    slot_2 = ({"class_id": 3, "class_name": "R2_GOOD", "confidence": 0.94}, (102, 0, 115, 320))
    slot_3_empty = ({"class_id": 0, "class_name": "NO_RING", "confidence": 0.99}, (102, 0, 115, 320))
    slot_3_ng = ({"class_id": 4, "class_name": "R2_NG", "confidence": 0.92}, (102, 0, 115, 320))

    # เคสที่ 1: เหลือ 2 ชิ้นดี + 1 ช่องว่าง -> ต้อง PASS เดินต่อได้
    ok, details = logic.evaluate_all_slots([slot_1, slot_2, slot_3_empty], round_index=1)
    print(f"ท้ายวันเหลือ 2 ชิ้น + 1 ว่าง -> Overall: {ok} | Details: {details}")

    # เคสที่ 2: 2 ชิ้นดี + 1 ชิ้นวางเบี้ยว (R2_NG) -> ต้อง FAIL หยุดเครื่อง
    ok, details = logic.evaluate_all_slots([slot_1, slot_2, slot_3_ng], round_index=1)
    print(f"2 ชิ้นดี + 1 ชิ้นเสีย     -> Overall: {ok} | Details: {details}")
