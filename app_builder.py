from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from handlers import (
    admin_ban_cmd,
    admin_reports_cmd,
    admin_stats_cmd,
    admin_unban_cmd,
    browse_cmd,
    cancel_cmd,
    delete_cmd,
    edit_bio_cmd,
    edit_filters_cmd,
    edit_photo_cmd,
    handle_photo,
    handle_web_app_data,
    handle_text,
    help_cmd,
    likes_cmd,
    miniapp_cmd,
    on_open_likes,
    on_reaction,
    on_setup_callback,
    pause_cmd,
    profile_cmd,
    remove_photo_cmd,
    resume_cmd,
    skip_photo_cmd,
    start,
    stats_cmd,
)


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("browse", browse_cmd))
    app.add_handler(CommandHandler("likes", likes_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("miniapp", miniapp_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("admin_stats", admin_stats_cmd))
    app.add_handler(CommandHandler("admin_reports", admin_reports_cmd))
    app.add_handler(CommandHandler("admin_ban", admin_ban_cmd))
    app.add_handler(CommandHandler("admin_unban", admin_unban_cmd))
    app.add_handler(CommandHandler("edit_bio", edit_bio_cmd))
    app.add_handler(CommandHandler("edit_photo", edit_photo_cmd))
    app.add_handler(CommandHandler("removephoto", remove_photo_cmd))
    app.add_handler(CommandHandler("edit_filters", edit_filters_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("skipphoto", skip_photo_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_setup_callback, pattern=r"^set:(gender|looking):(male|female|any)$"))
    app.add_handler(CallbackQueryHandler(on_reaction, pattern=r"^(like|skip|block|report):\d+$"))
    app.add_handler(CallbackQueryHandler(on_open_likes, pattern=r"^open_likes$"))
    app.add_handler(CallbackQueryHandler(on_open_likes, pattern=r"^likes:(open|like|skip|block|report)(:\d+)?$"))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app
