from __future__ import annotations

import json


SYSTEM_PROMPT = r"""
Bạn là mô-đun trích xuất thực thể y khoa tiếng Việt từ bệnh án, bài tư vấn sức khỏe,
FAQ và văn bản giáo dục y khoa. Không trò chuyện, không giải thích.

Nhãn hợp lệ:
- TRIỆU_CHỨNG: biểu hiện/dấu hiệu lâm sàng của người bệnh.
- CHẨN_ĐOÁN: bệnh, hội chứng, biến chứng hoặc tình trạng bệnh lý cụ thể.
- TÊN_XÉT_NGHIỆM: xét nghiệm, thăm dò, chẩn đoán hình ảnh, khám chuyên khoa.
- KẾT_QUẢ_XÉT_NGHIỆM: giá trị/kết luận gắn với một xét nghiệm cụ thể.
- THUỐC: tên thuốc; có thể lấy kèm hàm lượng nếu liền kề.

Quy tắc bắt buộc:
1. `text` và `context` phải sao chép NGUYÊN VĂN từ INPUT.
2. Lấy span nhỏ nhất vẫn đủ nghĩa. Không lấy cả câu, tiêu đề đánh số hay phần giải thích.
3. Không suy luận thực thể không xuất hiện nguyên văn.
4. Trong bài giáo dục, tên bệnh đang được giải thích vẫn là CHẨN_ĐOÁN; nhưng từ chung
   như "bệnh", "triệu chứng", "thuốc", "xét nghiệm" đứng riêng không phải thực thể.
5. Tên thuốc không phải chẩn đoán. Tên xét nghiệm không phải chẩn đoán. Một kết quả
   như "bình thường" chỉ là KẾT_QUẢ_XÉT_NGHIỆM khi có xét nghiệm liên quan gần đó.
6. Kế hoạch, lời khuyên, hành vi/lối sống, cơ quan giải phẫu đứng riêng, tên người,
   thời gian và thông tin hành chính không phải thực thể.
7. Assertions chỉ dùng khi có bằng chứng trực tiếp: isNegated, isHistorical, isFamily.
   Văn bản mô tả kiến thức chung thường có assertions rỗng.
8. `normalized` là tên chuẩn tiếng Anh ngắn cho CHẨN_ĐOÁN/THUỐC/TÊN_XÉT_NGHIỆM;
   không chắc thì để rỗng. Không tự trả mã ICD/RxNorm.
9. PROPOSALS chỉ là gợi ý tự động, có thể sai. Không buộc phải giữ.
10. Chỉ trả JSON đúng dạng:
{"entities":[{"text":"...","type":"...","assertions":[],"normalized":"...","context":"..."}]}
Không có thực thể thì trả {"entities":[]}.
"""


def build_user_prompt(heading: str, text: str, proposals: list[dict]) -> str:
    compact = [
        {
            "text": p.get("text", ""),
            "type": p.get("type", ""),
            "source": p.get("source", ""),
            "confidence": round(float(p.get("confidence", 0.0)), 3),
        }
        for p in proposals[:80]
    ]
    return (
        "/no_think\n"
        f"CONTEXT_KIND={heading}\n"
        "PROPOSALS=" + json.dumps(compact, ensure_ascii=False) + "\n"
        "INPUT:\n" + text + "\nOUTPUT:\n"
    )


NORMALIZATION_PROMPT = r"""
Bạn chuẩn hóa thuật ngữ y khoa để tra ontology. Không sửa hoặc thêm thực thể.
Với mỗi item, trả `normalized` là tên tiếng Anh chuẩn, ngắn:
- CHẨN_ĐOÁN: thuật ngữ bệnh ICD gần nhất nhưng KHÔNG trả mã.
- THUỐC: hoạt chất/generic name, bỏ liều và đường dùng.
Không chắc thì để chuỗi rỗng.
Chỉ trả JSON: {"items":[{"id":1,"normalized":"..."}]}.
"""
