"""
Маппинг между тредами Telegram и беседами VK
"""

# Маппинг: Telegram Thread ID → VK Peer ID
THREAD_TO_VK = {
    # thread_id: (peer_id, название)
    2: (2000000008, "Вопросы по ЖКХ"),
    3: (2000000001, "Соседский чат"),
    8: (2000000003, "О животных"),
    9: (2000000007, "Недвижимость | Куплю/Продам/Сдам/Арендую"),
    12: (2000000006, "Барахолка"),
    13: (2000000011, "Услуги по соседству"),
    255: (2000000005, "Авточат"),
    338: (2000000002, "Семья"),
    14101: (2000000012, "Фотографии"),
    22740: (2000000004, "Wishlist"),
    33787: (2000000010, "Юридические вопросы"),
    38364: (2000000009, "Игромания"),
}

# Треды которые публикуются на стену VK
WALL_THREADS = {
    1,  # Информация
}

# Обратный маппинг: VK Peer ID → Telegram Thread ID
VK_TO_THREAD = {peer_id: thread_id for thread_id, (peer_id, _) in THREAD_TO_VK.items()}

# Названия тредов
THREAD_NAMES = {thread_id: name for thread_id, (_, name) in THREAD_TO_VK.items()}

# Треды которые не синхронизируются с VK (только информационные в TG)
IGNORED_THREADS = {
    1377,   # Опросы
}

def get_vk_peer_id(thread_id: int) -> int:
    """Получить VK peer_id по Telegram thread_id"""
    if thread_id in THREAD_TO_VK:
        return THREAD_TO_VK[thread_id][0]
    return None

def get_telegram_thread_id(peer_id: int) -> int:
    """Получить Telegram thread_id по VK peer_id"""
    return VK_TO_THREAD.get(peer_id)

def get_thread_name(thread_id: int) -> str:
    """Получить название треда"""
    return THREAD_NAMES.get(thread_id, "Неизвестный тред")

def is_thread_synced(thread_id: int) -> bool:
    """Проверить синхронизируется ли тред с VK"""
    return thread_id not in IGNORED_THREADS and thread_id in THREAD_TO_VK

def is_wall_thread(thread_id: int) -> bool:
    """Проверить публикуется ли тред на стену VK"""
    return thread_id in WALL_THREADS