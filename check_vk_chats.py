#!/usr/bin/env python3
import vk_api

token = "vk1.a.GVn1wY5nBzlWu5puTrD_y2v8uCU3CA-BwbCVJPgi1xtzsz_VjTbkvqoUtnLDODxxtt2DPpF7g25-YEYFjXMj2mMuAtNKM2AtJE2_GUDokAWq-vJROc39ZSX-hkh4QwrLSUBqOv19_FdkO6fRmosOOdeVo9IZCOZckPxk5Iz_o9EoTb8_A6XbunlFqr_mwuIVGKoecnSDmBPQQ_ErnPoBxQ"
group_id = 223216432

vk_session = vk_api.VkApi(token=token)
vk = vk_session.get_api()

print("=" * 60)
print("СПИСОК ВСЕХ БЕСЕД ГДЕ УЧАСТВУЕТ СООБЩЕСТВО")
print("=" * 60)
print()

try:
    conversations = vk.messages.getConversations(count=200, filter='all')
    chat_list = []
    
    for item in conversations['items']:
        conversation = item['conversation']
        if conversation['peer']['type'] == 'chat':
            peer_id = conversation['peer']['id']
            chat_id = peer_id - 2000000000
            title = conversation.get('chat_settings', {}).get('title', 'Без названия')
            members_count = conversation.get('chat_settings', {}).get('members_count', 0)
            chat_list.append({
                'title': title,
                'peer_id': peer_id,
                'chat_id': chat_id,
                'members': members_count
            })
    
    if not chat_list:
        print("❌ Беседы не найдены!")
        print()
        print("Возможные причины:")
        print("1. Бот (сообщество) не добавлен ни в одну беседу")
        print("2. Токен не имеет прав 'Сообщения сообщества'")
        print("3. Сообщения сообщества отключены в настройках VK")
    else:
        print(f"Найдено бесед: {len(chat_list)}\n")
        
        for chat in chat_list:
            print(f"📝 {chat['title']}")
            print(f"   Peer ID: {chat['peer_id']}")
            print(f"   Chat ID: c{chat['chat_id']}")
            print(f"   Ссылка: https://vk.com/im?sel=c{chat['chat_id']}")
            print(f"   Участников: {chat['members']}")
            print()
        
        print("=" * 60)
        print("ГОТОВЫЙ МАППИНГ ДЛЯ vk_thread_mapping.py")
        print("=" * 60)
        print()
        print("THREAD_TO_VK = {")
        
        for chat in chat_list:
            thread_id = "???"
            
            if "Игромания" in chat['title'] or "игр" in chat['title'].lower():
                thread_id = "38364"
            elif "Соседский" in chat['title'] or "общ" in chat['title'].lower():
                thread_id = "3"
            elif "ЖКХ" in chat['title'] or "жкх" in chat['title'].lower():
                thread_id = "2"
            elif "животн" in chat['title'].lower():
                thread_id = "8"
            elif "Недвижим" in chat['title']:
                thread_id = "9"
            elif "Барахолка" in chat['title']:
                thread_id = "12"
            elif "Услуги" in chat['title']:
                thread_id = "13"
            elif "Авто" in chat['title']:
                thread_id = "255"
            elif "Семья" in chat['title']:
                thread_id = "338"
            elif "Wishlist" in chat['title']:
                thread_id = "22740"
            elif "Юридич" in chat['title']:
                thread_id = "33787"
            
            print(f'    {thread_id}: ({chat["peer_id"]}, "{chat["title"]}"),')
        
        print("}")

except Exception as e:
    print(f"❌ Ошибка: {e}")
