
    
  
                continue
            if timestamp_ms < cutoff_ms:
                batch.delete(doc.reference)
                batch_count += 1
                deleted_count += 1
                if batch_count >= 400:
                    batch.commit()
                    batch = db.batch()
                    batch_count = 0
        if batch_count > 0:
            batch.commit()
        print(f"TYPY: czyszczenie zakończone. Usunięto: {deleted_count}")
        return deleted_count
    except Exception as e:
        print(f"ERROR TYPY cleanup: {e}")
        return 0

@tasks.loop(seconds=30)
async def check_typy_cleanup():
    try:
        now = datetime.now(WARSAW)
        current_week_start = get_current_week_start()
        current_week_key = current_week_start.strftime("%Y-%m-%d")
        system_ref = db.collection("typy").document(TYPY_SYSTEM_DOC)
        system_doc = system_ref.get()
        system_data = {}
        if system_doc.exists:
            system_data = system_doc.to_dict() or {}
        initialized = bool(system_data.get("initialized"))
        cleanup_week = system_data.get("cleanup_week")
        if not initialized:
            deleted_count = cleanup_old_typy(current_week_start)
            system_ref.set({
                "initialized": True,
                "cleanup_week": current_week_key,
                "last_cleanup_at": firestore.SERVER_TIMESTAMP,
                "last_deleted_count": deleted_count
            }, merge=True)
            return
        if now.weekday() != 0 or now.hour < 12 or cleanup_week == current_week_key:
            return
        deleted_count = cleanup_old_typy(current_week_start)
        system_ref.set({
            "cleanup_week": current_week_key,
            "last_cleanup_at": firestore.SERVER_TIMESTAMP,
            "last_deleted_count": deleted_count
        }, merge=True)
    except Exception as e:
        print(f"ERROR typy cleanup: {e}")

async def sync_members():
    try:
        guild_id = int(os.getenv("GUILD_ID") or "1515466354113777715")
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        members = []
        async for member in guild.fetch_members(limit=None):
            members.append(member)
        collection = db.collection("czlonkowie")
        batch = db.batch()
        batch_count = 0
        for member in members:
            if member.bot:
                continue
            doc_ref = collection.document(str(member.id))
            batch.set(doc_ref, {
                "discord_id": str(member.id),
                "username": member.name,
                "display_name": member.display_name,
                "avatar": str(member.display_avatar.url) if member.display_avatar else "",
            }, merge=True)
            ensure_users_doc(str(member.id), member.display_name)
            batch_count += 1
            if batch_count >= 400:
                batch.commit()
                batch = db.batch()
                batch_count = 0
        if batch_count > 0:
            batch.commit()
    except Exception as e:
        print(f"ERROR czlonkowie: {e}")

@tasks.loop(hours=6)
async def sync_members_loop():
    await sync_members()

@bot.event
async def on_member_join(member):
    try:
        if member.bot:
            return
        db.collection("czlonkowie").document(str(member.id)).set({
            "discord_id": str(member.id),
            "username": member.name,
            "display_name": member.display_name,
            "avatar": str(member.display_avatar.url) if member.display_avatar else "",
            "active": True,
        }, merge=True)
        ensure_users_doc(str(member.id), member.display_name)
    except Exception as e:
        print(f"ERROR on_member_join: {e}")

@bot.event
async def on_ready():
    print(f"READY {bot.user}")
    if not check_lista.is_running():
        check_lista.start()
    if not check_wyniki_loop.is_running():
        check_wyniki_loop.start()
    if not check_typy_cleanup.is_running():
        check_typy_cleanup.start()
    if not sync_members_loop.is_running():
        sync_members_loop.start()
    await sync_members()

start_health_server()

_raw_token = os.getenv("DISCORD_TOKEN") or ""
_token = _raw_token.strip().strip('"').strip("'").strip()
if not _token:
    print("FATAL: DISCORD_TOKEN pusty - ustaw w Hostless Environment variables")
    while True:
        time.sleep(60)

print(f"DEBUG TOKEN len={len(_token)} kropki={_token.count('.')}")

try:

