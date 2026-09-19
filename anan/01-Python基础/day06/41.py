def validate_result(result, schema):
    """
    校验模型返回结果是否符合指定 Schema。

    返回：
        (bool, errors)
        bool   : 是否通过校验
        errors : 所有错误信息列表
    """
    errors = []

    # 1. 校验 required 字段
    for field in schema.get("required", []):
        if field not in result:
            errors.append(f"缺少必填字段：{field}")

    # 2. 校验 properties 中的 enum 和 minItems
    properties = schema.get("properties", {})

    for field, rules in properties.items():
        # 字段不存在时跳过，是否必填由 required 负责
        if field not in result:
            continue

        value = result[field]

        # enum 校验
        if "enum" in rules:
            if value not in rules["enum"]:
                errors.append(
                    f"字段 {field} 的值 {value!r} 不在允许范围 "
                    f"{rules['enum']} 中"
                )

        # minItems 校验
        if "minItems" in rules:
            if not isinstance(value, list):
                errors.append(f"字段 {field} 应为数组")
            elif len(value) < rules["minItems"]:
                errors.append(
                    f"字段 {field} 至少需要 "
                    f"{rules['minItems']} 个元素，实际为 {len(value)} 个"
                )

    return len(errors) == 0, errors


# =========================
# Schema
# =========================

schema = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["approved", "rejected", "manual_review"]
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"]
        },
        "reasons": {
            "type": "array",
            "minItems": 1
        }
    },
    "required": [
        "decision",
        "risk_level",
        "reasons"
    ]
}


# =========================
# 合法结果
# =========================

valid_result = {
    "decision": "approved",
    "risk_level": "low",
    "reasons": [
        "收入稳定",
        "近期无逾期记录"
    ]
}

ok, errors = validate_result(valid_result, schema)

print("合法结果：")
print(ok)
print(errors)


# =========================
# 违规结果
# =========================

invalid_result = {
    # 缺少 decision
    "risk_level": "very_high",   # enum 不合法
    "reasons": []                # 不满足 minItems=1
}

ok, errors = validate_result(invalid_result, schema)

print("\n违规结果：")
print(ok)
print(errors)


'''
合法结果：

True

[]


违规结果：
False

['缺少必填字段：decision', "字段 risk_level 的值 'very_high' 不在允许范围 ['low', 'medium', 'high'] 中", '字段 reasons 至少需要 1 个元素，实际为 0 个']
'''