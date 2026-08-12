ENTITY_TYPES = [
    "TRIỆU_CHỨNG", "CHẨN_ĐOÁN", "THUỐC",
    "TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM",
]
LABELS = ["O"] + [f"{prefix}-{typ}" for typ in ENTITY_TYPES for prefix in ("B", "I")]
LABEL2ID = {label: index for index, label in enumerate(LABELS)}
ID2LABEL = {index: label for label, index in LABEL2ID.items()}
