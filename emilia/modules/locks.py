import html
from typing import Optional, List

import telegram.ext as tg
from telegram import Message, Chat, Update, Bot, ParseMode, User, MessageEntity
from telegram import TelegramError
from telegram.error import BadRequest
from telegram.ext import CommandHandler, MessageHandler, Filters
from telegram.ext.dispatcher import run_async
from telegram.utils.helpers import mention_html

from alphabet_detector import AlphabetDetector

import emilia.modules.sql.locks_sql as sql
from emilia import dispatcher, SUDO_USERS, LOGGER, spamfilters
from emilia.modules.disable import DisableAbleCommandHandler
from emilia.modules.helper_funcs.chat_status import can_delete, is_user_admin, user_not_admin, user_admin, \
    bot_can_delete, is_bot_admin
from emilia.modules.helper_funcs.filters import CustomFilters
from emilia.modules.log_channel import loggable
from emilia.modules.sql import users_sql
from emilia.modules.connection import connected

ad = AlphabetDetector()

LOCK_TYPES = {'sticker': Filters.sticker,
              'audio': Filters.audio,
              'voice': Filters.voice,
              'document': Filters.document,
              'video': Filters.video,
              'contact': Filters.contact,
              'photo': Filters.photo,
              'gif': Filters.document & CustomFilters.mime_type("video/mp4"),
              'url': Filters.entity(MessageEntity.URL) | Filters.caption_entity(MessageEntity.URL),
              'bots': Filters.status_update.new_chat_members,
              'forward': Filters.forwarded,
              'game': Filters.game,
              'location': Filters.location,
              'rtl': 'rtl',
              }

GIF = Filters.document & CustomFilters.mime_type("video/mp4")
OTHER = Filters.game | Filters.sticker | GIF
MEDIA = Filters.audio | Filters.document | Filters.video | Filters.voice | Filters.photo
MESSAGES = Filters.text | Filters.contact | Filters.location | Filters.venue | Filters.command | MEDIA | OTHER
PREVIEWS = Filters.entity("url")

RESTRICTION_TYPES = {'messages': MESSAGES,
                     'media': MEDIA,
                     'other': OTHER,
                     # 'previews': PREVIEWS, # NOTE: this has been removed cos its useless atm.
                     'all': Filters.all}

PERM_GROUP = 1
REST_GROUP = 2


class CustomCommandHandler(tg.CommandHandler):
    def __init__(self, command, callback, **kwargs):
        super().__init__(command, callback, **kwargs)

    def check_update(self, update):
        return super().check_update(update) and not (
                sql.is_restr_locked(update.effective_chat.id, 'messages') and not is_user_admin(update.effective_chat,
                                                                                                update.effective_user.id))


tg.CommandHandler = CustomCommandHandler


# NOT ASYNC
def restr_members(bot, chat_id, members, messages=False, media=False, other=False, previews=False):
    for mem in members:
        if mem.user in SUDO_USERS:
            pass
        try:
            bot.restrict_chat_member(chat_id, mem.user,
                                     can_send_messages=messages,
                                     can_send_media_messages=media,
                                     can_send_other_messages=other,
                                     can_add_web_page_previews=previews)
        except TelegramError:
            pass


# NOT ASYNC
def unrestr_members(bot, chat_id, members, messages=True, media=True, other=True, previews=True):
    for mem in members:
        try:
            bot.restrict_chat_member(chat_id, mem.user,
                                     can_send_messages=messages,
                                     can_send_media_messages=media,
                                     can_send_other_messages=other,
                                     can_add_web_page_previews=previews)
        except TelegramError:
            pass


@run_async
def locktypes(bot: Bot, update: Update):
    spam = spamfilters(update.effective_message.text, update.effective_message.from_user.id, update.effective_chat.id, update.effective_message)
    if spam == True:
        return
    locklist = list(LOCK_TYPES) + list(RESTRICTION_TYPES)
    locklist.sort()
    update.effective_message.reply_text("\n - ".join(["Jenis kunci yang tersedia adalah: "] + locklist))


@user_admin
@loggable
def lock(bot: Bot, update: Update, args: List[str]) -> str:
    spam = spamfilters(update.effective_message.text, update.effective_message.from_user.id, update.effective_chat.id, update.effective_message)
    if spam == True:
        return
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    message = update.effective_message  # type: Optional[Message]
    args = args.split()
    if can_delete(chat, bot.id) or update.effective_message.chat.type == "private":
        if len(args) >= 1:
            if args[0] in LOCK_TYPES:
                # Connection check
                conn = connected(bot, update, chat, user.id, need_admin=True)
                if conn:
                    chat = dispatcher.bot.getChat(conn)
                    chat_id = conn
                    chat_name = chat.title
                    text = "Terkunci *{}* pesan untuk semua non-admin pada *{}*!".format(args[0], chat_name)
                else:
                    if update.effective_message.chat.type == "private":
                        update.effective_message.reply_text("Anda bisa lakukan command ini pada grup, bukan pada PM")
                        return ""
                    chat = update.effective_chat
                    chat_id = update.effective_chat.id
                    chat_name = update.effective_message.chat.title
                    text = "Terkunci *{}* pesan untuk semua non-admin!".format(args[0])
                sql.update_lock(chat.id, args[0], locked=True)
                message.reply_text(text, parse_mode="markdown")

                return "<b>{}:</b>" \
                       "\n#LOCK" \
                       "\n<b>Admin:</b> {}" \
                       "\nTerkunci <code>{}</code>.".format(html.escape(chat.title),
                                                          mention_html(user.id, user.first_name), args[0])

            elif args[0] in RESTRICTION_TYPES:
                # Connection check
                conn = connected(bot, update, chat, user.id, need_admin=True)
                if conn:
                    chat = dispatcher.bot.getChat(conn)
                    chat_id = conn
                    chat_name = chat.title
                    text = "Terkunci *{}* pesan untuk semua non-admin pada *{}*!".format(args[0], chat_name)
                else:
                    if update.effective_message.chat.type == "private":
                        update.effective_message.reply_text("Anda bisa lakukan command ini pada grup, bukan pada PM")
                        return ""
                    chat = update.effective_chat
                    chat_id = update.effective_chat.id
                    chat_name = update.effective_message.chat.title
                    text = "Terkunci *{}* pesan untuk semua non-admin!".format(args[0])
                sql.update_restriction(chat.id, args[0], locked=True)
                if args[0] == "previews":
                    members = users_sql.get_chat_members(str(chat.id))
                    restr_members(bot, chat.id, members, messages=True, media=True, other=True)

                message.reply_text(text, parse_mode="markdown")
                return "<b>{}:</b>" \
                       "\n#LOCK" \
                       "\n<b>Admin:</b> {}" \
                       "\nTerkunci <code>{}</code>.".format(html.escape(chat.title),
                                                          mention_html(user.id, user.first_name), args[0])

            else:
                message.reply_text("Apa yang Anda coba untuk kunci...? Coba /locktypes untuk daftar kunci yang dapat dikunci")
        else:
           message.reply_text("Apa yang Anda ingin kunci...?")

    else:
        message.reply_text("Saya bukan admin, atau tidak punya hak menghapus.")

    return ""


@run_async
@user_admin
@loggable
def unlock(bot: Bot, update: Update, args: List[str]) -> str:
    spam = spamfilters(update.effective_message.text, update.effective_message.from_user.id, update.effective_chat.id, update.effective_message)
    if spam == True:
        return
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    message = update.effective_message  # type: Optional[Message]
    args = args.split()
    if is_user_admin(chat, message.from_user.id):
        if len(args) >= 1:
            if args[0] in LOCK_TYPES:
                # Connection check
                conn = connected(bot, update, chat, user.id, need_admin=True)
                if conn:
                    chat = dispatcher.bot.getChat(conn)
                    chat_id = conn
                    chat_name = chat.title
                    text = "Tidak terkunci *{}* untuk semua orang pada *{}*!".format(args[0], chat_name)
                else:
                    if update.effective_message.chat.type == "private":
                        update.effective_message.reply_text("Anda bisa lakukan command ini pada grup, bukan pada PM")
                        return ""
                    chat = update.effective_chat
                    chat_id = update.effective_chat.id
                    chat_name = update.effective_message.chat.title
                    text = "Tidak terkunci *{}* untuk semua orang!".format(args[0])
                sql.update_lock(chat.id, args[0], locked=False)
                message.reply_text(text, parse_mode="markdown")
                return "<b>{}:</b>" \
                       "\n#UNLOCK" \
                       "\n<b>Admin:</b> {}" \
                       "\nTidak terkunci <code>{}</code>.".format(html.escape(chat.title),
                                                            mention_html(user.id, user.first_name), args[0])

            elif args[0] in RESTRICTION_TYPES:
                # Connection check
                conn = connected(bot, update, chat, user.id, need_admin=True)
                if conn:
                    chat = dispatcher.bot.getChat(conn)
                    chat_id = conn
                    chat_name = chat.title
                    text = "Tidak terkunci *{}* untuk semua orang pada *{}*!".format(args[0], chat_name)
                else:
                    if update.effective_message.chat.type == "private":
                        update.effective_message.reply_text("Anda bisa lakukan command ini pada grup, bukan pada PM")
                        return ""
                    chat = update.effective_chat
                    chat_id = update.effective_chat.id
                    chat_name = update.effective_message.chat.title
                    text = "Tidak terkunci *{}* untuk semua orang!".format(args[0])
                sql.update_restriction(chat.id, args[0], locked=False)
                """
                members = users_sql.get_chat_members(chat.id)
                if args[0] == "messages":
                    unrestr_members(bot, chat.id, members, media=False, other=False, previews=False)

                elif args[0] == "media":
                    unrestr_members(bot, chat.id, members, other=False, previews=False)

                elif args[0] == "other":
                    unrestr_members(bot, chat.id, members, previews=False)

                elif args[0] == "previews":
                    unrestr_members(bot, chat.id, members)

                elif args[0] == "all":
                    unrestr_members(bot, chat.id, members, True, True, True, True)
                """
                message.reply_text(text, parse_mode="markdown")
                
                return "<b>{}:</b>" \
                       "\n#UNLOCK" \
                       "\n<b>Admin:</b> {}" \
                       "\nTidak terkunci <code>{}</code>.".format(html.escape(chat.title),
                                                            mention_html(user.id, user.first_name), args[0])
            else:
                message.reply_text("Apa yang Anda coba untuk membuka kunci...? Coba /locktypes untuk daftar kunci yang dapat dikunci")

        else:
            message.reply_text("Apa yang Anda coba untuk buka kunci...?")

    return ""


@run_async
@user_not_admin
def del_lockables(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]
    message = update.effective_message  # type: Optional[Message]
    
    for lockable, filter in LOCK_TYPES.items():
        if lockable == "rtl":
            if sql.is_locked(chat.id, lockable) and can_delete(chat, bot.id):
                if message.caption:
                    check = ad.detect_alphabet(u'{}'.format(message.caption))
                    if 'ARABIC' in check:
                        try:
                            message.delete()
                        except BadRequest as excp:
                            if excp.message == "Message to delete not found":
                                pass
                            else:
                                LOGGER.exception("ERROR in lockables")
                if message.text:
                    check = ad.detect_alphabet(u'{}'.format(message.text))
                    if 'ARABIC' in check:
                        try:
                            message.delete()
                        except BadRequest as excp:
                            if excp.message == "Message to delete not found":
                                pass
                            else:
                                LOGGER.exception("ERROR in lockables")
            break
        if filter(update) and sql.is_locked(chat.id, lockable) and can_delete(chat, bot.id):
            if lockable == "bots":
                new_members = update.effective_message.new_chat_members
                for new_mem in new_members:
                    if new_mem.is_bot:
                        if not is_bot_admin(chat, bot.id):
                            message.reply_text("Saya melihat bot, dan saya diberitahu untuk menghentikan mereka bergabung... "
                                               "tapi saya bukan admin!")
                            return

                        chat.kick_member(new_mem.id)
                        message.reply_text("Hanya admin yang diizinkan menambahkan bot ke obrolan ini! Keluar dari sini!")
            else:
                try:
                    message.delete()
                except BadRequest as excp:
                    if excp.message == "Message to delete not found":
                        pass
                    else:
                        LOGGER.exception("ERROR in lockables")
 
            break
    

@run_async
@user_not_admin
def rest_handler(bot: Bot, update: Update):
    msg = update.effective_message  # type: Optional[Message]
    chat = update.effective_chat  # type: Optional[Chat]
    for restriction, filter in RESTRICTION_TYPES.items():
        if filter(update) and sql.is_restr_locked(chat.id, restriction) and can_delete(chat, bot.id):
            try:
                msg.delete()
            except BadRequest as excp:
                if excp.message == "Message to delete not found":
                    pass
                else:
                    LOGGER.exception("ERROR in restrictions")
            break


def build_lock_message(chat_id):
    locks = sql.get_locks(chat_id)
    restr = sql.get_restr(chat_id)
    if not (locks or restr):
        res = "Tidak ada kunci saat ini dalam obrolan ini."
    else:
        res = "Ini adalah kunci dalam obrolan ini:"
        locklist = []
        if locks:
            locklist.append("sticker = `{}`".format(locks.sticker))
            locklist.append("audio = `{}`".format(locks.audio))
            locklist.append("voice = `{}`".format(locks.voice))
            locklist.append("document = `{}`".format(locks.document))
            locklist.append("video = `{}`".format(locks.video))
            locklist.append("contact = `{}`".format(locks.contact))
            locklist.append("photo = `{}`".format(locks.photo))
            locklist.append("gif = `{}`".format(locks.gif))
            locklist.append("url = `{}`".format(locks.url))
            locklist.append("bots = `{}`".format(locks.bots))
            locklist.append("forward = `{}`".format(locks.forward))
            locklist.append("game = `{}`".format(locks.game))
            locklist.append("location = `{}`".format(locks.location))
            locklist.append("rtl = `{}`".format(locks.rtl))
            """
            res += "\n - sticker = `{}`" \
                   "\n - audio = `{}`" \
                   "\n - voice = `{}`" \
                   "\n - document = `{}`" \
                   "\n - video = `{}`" \
                   "\n - contact = `{}`" \
                   "\n - photo = `{}`" \
                   "\n - gif = `{}`" \
                   "\n - url = `{}`" \
                   "\n - bots = `{}`" \
                   "\n - forward = `{}`" \
                   "\n - game = `{}`" \
                   "\n - location = `{}`" \
                   "\n - rtl = `{}` ".format(locks.sticker, locks.audio, locks.voice, locks.document,
                                             locks.video, locks.contact, locks.photo, locks.gif, locks.url, locks.bots,
                                             locks.forward, locks.game, locks.location, locks.rtl)
            """
        if restr:
            locklist.append("messages = `{}`".format(restr.messages))
            locklist.append("media = `{}`".format(restr.media))
            locklist.append("other = `{}`".format(restr.other))
            locklist.append("previews = `{}`".format(restr.preview))
            locklist.append("all = `{}`".format(all([restr.messages, restr.media, restr.other, restr.preview])))
            """
            res += "\n - messages = `{}`" \
                   "\n - media = `{}`" \
                   "\n - other = `{}`" \
                   "\n - previews = `{}`" \
                   "\n - all = `{}`".format(restr.messages, restr.media, restr.other, restr.preview,
                                            all([restr.messages, restr.media, restr.other, restr.preview]))
            """
        # Ordering lock list
        locklist.sort()
        # Building lock list string
        for x in locklist:
            res += "\n - {}".format(x)
    return res


@run_async
@user_admin
def list_locks(bot: Bot, update: Update):
    spam = spamfilters(update.effective_message.text, update.effective_message.from_user.id, update.effective_chat.id, update.effective_message)
    if spam == True:
        return
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user

    # Connection check
    conn = connected(bot, update, chat, user.id, need_admin=True)
    if conn:
        chat = dispatcher.bot.getChat(conn)
        chat_id = conn
        chat_name = chat.title
    else:
        if update.effective_message.chat.type == "private":
            update.effective_message.reply_text("Anda bisa lakukan command ini pada grup, bukan pada PM")
            return ""
        chat = update.effective_chat
        chat_id = update.effective_chat.id
        chat_name = update.effective_message.chat.title

    res = build_lock_message(chat.id)
    if conn:
        res = res.replace('obrolan ini', '*{}*'.format(chat_name))

    update.effective_message.reply_text(res, parse_mode=ParseMode.MARKDOWN)


def __import_data__(chat_id, data):
    # set chat locks
    locks = data.get('locks', {})
    for itemlock in locks:
        if itemlock in LOCK_TYPES:
          sql.update_lock(chat_id, itemlock, locked=True)
        elif itemlock in RESTRICTION_TYPES:
          sql.update_restriction(chat_id, itemlock, locked=True)
        else:
          pass


def __migrate__(old_chat_id, new_chat_id):
    sql.migrate_chat(old_chat_id, new_chat_id)


def __chat_settings__(chat_id, user_id):
    return build_lock_message(chat_id)


__help__ = """
 - /locktypes: daftar kemungkinan tipe kunci

*Admin only:*
 - /lock <type>: mengunci sesuatu dengan jenis tertentu (tidak tersedia secara pribadi)
 - /unlock <type>: membuka kunci sesuatu dengan jenis tertentu (tidak tersedia secara pribadi)
 - /locks: daftar kunci saat ini di obrolan ini.

Kunci dapat digunakan untuk membatasi pengguna grup.
seperti:
Mengunci url akan otomatis menghapus semua pesan dengan url yang belum masuk daftar putih, mengunci stiker akan menghapus semua \
stiker, dll.
Mengunci bot akan menghentikan non-admin menambahkan bots ke obrolan.
"""

__mod_name__ = "Kunci"

LOCKTYPES_HANDLER = DisableAbleCommandHandler("locktypes", locktypes)
LOCK_HANDLER = CommandHandler("lock", lock, pass_args=True)#, filters=Filters.group)
UNLOCK_HANDLER = CommandHandler("unlock", unlock, pass_args=True)#, filters=Filters.group)
LOCKED_HANDLER = CommandHandler("locks", list_locks)#, filters=Filters.group)

dispatcher.add_handler(LOCK_HANDLER)
dispatcher.add_handler(UNLOCK_HANDLER)
dispatcher.add_handler(LOCKTYPES_HANDLER)
dispatcher.add_handler(LOCKED_HANDLER)

dispatcher.add_handler(MessageHandler(Filters.all & Filters.group, del_lockables), PERM_GROUP)
dispatcher.add_handler(MessageHandler(Filters.all & Filters.group, rest_handler), REST_GROUP)
