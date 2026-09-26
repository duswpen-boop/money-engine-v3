"""Visual subjects follow verified policy details and article sections."""

import re

from PIL import Image, ImageChops, ImageStat

from .quality import verified_values

SETTINGS = {
    "CARD_FEES": (("Korean small store owner and a real card terminal", "contemporary Korean small retail counter", "accepting a card payment naturally"),
                  ("shop owner reviewing card payment activity", "modern small shop office desk", "checking a card terminal beside a laptop with screen turned away"),
                  ("Korean neighborhood merchant", "contemporary convenience store counter", "serving an ordinary customer using contactless payment")),
    "EV": (("electric vehicle at an apartment charging bay", "contemporary apartment EV charging area", "checking charging connection"),
           ("electric vehicle applicant", "modern Korean home work desk", "reviewing the application on a laptop with screen turned away"),
           ("electric vehicles in daily use", "modern Korean public parking lot", "parking beside a charging station")),
    "SMALL_BUSINESS": (("owner of a small contemporary Korean shop", "realistic cafe or small store interior", "opening the shop"),
                       ("small business applicant", "quiet shop counter", "reviewing application information on a laptop with screen turned away"),
                       ("operating small business", "contemporary cafe or neighborhood store", "serving a customer naturally")),
    "HOUSING": (("Korean apartment residence", "contemporary apartment exterior", "arriving at the entrance"),
                ("housing applicant", "bright apartment interior", "inspecting the home"),
                ("Korean residential community", "modern apartment courtyard", "walking through the neighborhood")),
    "FINANCE": (("small business owner", "contemporary Korean office", "working at a desk"),
                ("finance applicant", "quiet office desk", "reviewing information on a laptop with screen turned away"),
                ("small business operation", "realistic small shop", "working with ordinary equipment")),
    "GENERAL": (("Korean applicant relevant to this policy", "contemporary Korean everyday workplace", "engaged in an ordinary relevant activity"),
                ("Korean applicant", "modern home or office desk", "reviewing application information on a laptop with screen turned away"),
                ("policy target in daily life", "contemporary Korean neighborhood", "using the relevant service")),
}

AVOID = ("all readable text including Korean signs, dates and figures; money; logos; government documents; "
         "app UI; staged advertising pose; plastic skin; distorted hands or bodies; crowd; cinematic lighting; "
         "old alley or traditional market unless the verified subject requires one")


def category_for(content: dict) -> str:
    values = verified_values(content.get("research") or {})
    topic = " ".join(str(values.get(key) or "") for key in ("program_name", "key_changes", "eligibility"))
    if "카드수수료" in topic or "카드 수수료" in topic:
        return "CARD_FEES"
    if any(token in topic for token in ("전기차", "전기 트럭", "EV", "전기자동차")):
        return "EV"
    if any(token in topic for token in ("LH", "주택", "임대", "아파트")):
        return "HOUSING"
    if any(token in topic for token in ("금융", "융자", "대출", "자금")):
        return "FINANCE"
    if any(token in topic for token in ("소상공인", "자영업", "음식점", "가맹점")):
        return "SMALL_BUSINESS"
    return "GENERAL"


def image_scene_plan(content: dict) -> dict:
    article = (content.get("outputs") or {}).get("ARTICLE_PLAN") or {}
    sections = article.get("sections") or []
    if len(sections) < 2 or not content.get("body"):
        raise ValueError("근거 있는 본문을 완성한 다음 이미지를 계획할 수 있습니다.")
    values = verified_values(content.get("research") or {})
    category = category_for(content)
    if category == "GENERAL":
        raise ValueError("소재와 직접 연결되는 검증된 이미지 장면을 정할 수 없습니다.")
    scene_rows = SETTINGS[category]
    roles = ("COVER", "ACTION", "CONTEXT")
    action_section = next((entry["heading"] for entry in sections if entry.get("intent") in ("HOW", "DOCUMENT", "WHEN")), sections[0]["heading"])
    context_section = next((entry["heading"] for entry in sections if entry.get("intent") in ("WHO", "MONEY", "WHERE")), sections[-1]["heading"])
    slug = str((content.get("outputs", {}).get("KEYWORD_MAP") or {}).get("image_slug") or "")
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")[:70] or "money-engine-" + content["id"][:8]
    program = str(values.get("program_name") or "")
    images = []
    for index, role in enumerate(roles):
        subject, location, action = scene_rows[index]
        section = "전체" if role == "COVER" else action_section if role == "ACTION" else context_section
        visual_goal = f"Communicate the subject of {program} and the {role.lower()} role through the real activity"
        prompt = ("Photorealistic Korean editorial documentary photography, contemporary South Korea, 16:9. "
                  f"Subject: {subject}. Location: {location}. Action: {action}. "
                  f"Goal: {visual_goal}. Natural daylight, candid composition, realistic proportions, subtle depth of field, "
                  "restrained color grading, news editorial photo feeling, imperfect ordinary surroundings. "
                  f"Avoid: {AVOID}. No text anywhere in the image, no writing on screens or signs.")
        images.append({"slot": index + 1, "role": role, "related_section": section, "subject": subject,
                       "location": location, "action": action, "visual_goal": visual_goal, "avoid": AVOID,
                       "scene": f"{location}; {action}", "prompt": prompt,
                       "filename": f"{slug}-{role.lower()}.webp",
                       "insert_after": "본문 도입부 뒤" if role == "COVER" else f"'{section}' 뒤"})
    if len({item["scene"] for item in images}) != 3:
        raise ValueError("이미지 장면이 중복됐습니다.")
    return {"category": category, "images": images}


def duplicate_image(candidate: str, other_paths: list[str]) -> bool:
    with Image.open(candidate) as source:
        thumbnail = source.convert("RGB").resize((24, 24))
        for path in other_paths:
            try:
                with Image.open(path) as other:
                    if ImageStat.Stat(ImageChops.difference(thumbnail, other.convert("RGB").resize((24, 24)))).mean[0] < 2 and \
                       sum(ImageStat.Stat(ImageChops.difference(thumbnail, other.convert("RGB").resize((24, 24)))).mean) < 6:
                        return True
            except OSError:
                continue
    return False
