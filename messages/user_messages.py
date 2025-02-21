def cancel():
    return "↩️Cancel"


def welcome_message():
    return ("هلو احمد ")


def settings():
    return (
        "<b>⚙️Settings</b>\nUsing the buttons below, you can customize the bot's functionalities. Keep in mind that all the changes made will only apply to you.")


def captions_settings():
    return (
        "<b>✏️Captions</b>\nChoose if you want to add a short description to downloaded content. Keep in mind that some extractors still don't support this feature.")


def captions(user_captions, post_caption, bot_url):
    if user_captions == "on" and post_caption is not None:
        return ('{post_caption}\n\n<a href="{bot_url}">حصريات </a>').format(post_caption=post_caption,
                                                                                        bot_url=bot_url)
    else:
        return ('<a href="{bot_url}">حصريات</a>').format(bot_url=bot_url)


def join_group(chat_title):
    return ("Hi! Thank you for adding me to <b>'{chat_title}'</b>!\nHave a nice day!").format(chat_title=chat_title)
