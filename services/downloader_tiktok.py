import os
import re

import requests
from bs4 import BeautifulSoup as bs

from helper import random_ua, get_content


class DownloaderTikTok:
    def __init__(self, output_dir: str, output_name: str):
        self.output_dir = output_dir
        self.output_name = output_name

    def tiktapiocom(self, url: str):
        try:
            ses = requests.Session()
            ses.headers.update({"User-Agent": random_ua()})
            res = ses.get("https://tiktokio.com/id/")
            open("../hasil.html", "w", encoding="utf-8").write(res.text)
            prefix = re.search(
                r'<input type="hidden" name="prefix" value="(.*?)"/>', res.text
            ).group(1)
            data = {"prefix": prefix, "vid": url}
            ses.headers.update(
                {
                    "Content-Length": str(len(str(data))),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Hx-Current-Url": "https://tiktokio.com/",
                    "Hx-Request": "true",
                    "Hx-Target": "tiktok-parse-result",
                    "Hx-Trigger": "search-btn",
                }
            )
            res = ses.post("https://tiktokio.com/api/v1/tk-htmx", data=data)
            parser = bs(res.text, "html.parser")
            video_url = (
                parser.find_all("div", attrs={"class": "tk-down-link"})[0]
                .find("a")
                .get("href")
            )
            res = get_content(video_url, self.output_dir, self.output_name)
            try:
                os.remove("hasil.html")
            except:
                pass
            return res

        except Exception as e:
            print(f"tiktapiocom error : {e}")
            try:
                os.remove("hasil.html")
            except:
                pass
            return False

    def tikmatecc(self, url: str):
        try:
            headers = {
                "Host": "europe-west3-instadown-314417.cloudfunctions.net",
                "User-Agent": "socialdownloader.p.rapidapi.com",
                "Accept": "*/*",
                "Accept-Language": "ar",
                "Accept-Encoding": "gzip, deflate",
            }
            api = (
                    "https://europe-west3-instadown-314417.cloudfunctions.net/yt-dlp-1?url="
                    + url
            )
            res = requests.get(api, headers=headers)
            if res.text[0] != "{":
                return False

            error = res.json()["null"] or res.json()["error"] or res.json()["Error"]
            if error:
                return False

            video_url = res.json()["LINKS"]
            res = get_content(video_url, self.output_dir, self.output_name)
            return res

        except Exception as e:
            print(f"tikmatecc error : {e}")
            return False
