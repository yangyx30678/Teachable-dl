import argparse
import json
import logging
import os
import re
import string
import sys
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import selenium.webdriver.support.expected_conditions as EC
import wget
import yt_dlp
from selenium.common import TimeoutException
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.remote.webdriver import By
from selenium.webdriver.support.wait import WebDriverWait
from seleniumbase import Driver

from dotenv import load_dotenv
load_dotenv(verbose=True)
import os
URL = os.getenv("URL")
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

def create_folder(course_title):
    root_path = os.path.abspath(os.getcwd())
    course_path = os.path.join(root_path, "courses", course_title)
    os.makedirs(course_path, exist_ok=True)
    return course_path


def clean_string(data):
    logging.debug("Cleaning string: " + data)
    # Remove all non-ASCII characters (including emojis)
    data = data.encode('ascii', 'ignore').decode('ascii')
    # Replace specific characters with "-"
    return data.replace("\n", "-").replace(" ", "-").replace(":", "-") \
        .replace("/", "-").replace("|", "-").replace("*", "").replace("?", "-").replace("<", "-") \
        .replace(">", "-").replace("\"", "-").replace("\\", "-")


def truncate_title_to_fit_file_name(title, max_file_name_length=250):
    # the file name length should not be too long
    # truncate the title to accommodate the max used file extension length and lecture index prefix
    max_title_length = max_file_name_length - len(".mp4.part-Frag0000.part") - 3
    if len(title) > max_title_length:
        turncated_title = title[:max_title_length]
        logging.warning("Truncating title: " + turncated_title)
        return turncated_title
    return title


class TeachableDownloader:
    def __init__(self, verbose_arg=False, complete_lecture_arg=False, user_agent_arg=None, timeout_arg=3):
        self.driver = Driver(uc=True, headed=True)
        self.headers = {
            "User-Agent": user_agent_arg,
            "Origin": "https://player.hotmart.com",
            "Referer": "https://player.hotmart.com"
        }
        self.verbose = verbose_arg
        self._complete_lecture = complete_lecture_arg
        self.global_timeout = timeout_arg

    def check_elem_exists(self, by, selector, timeout):
        try:
            WebDriverWait(self.driver, timeout=self.global_timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except NoSuchElementException:
            return False
        except TimeoutException:
            return False
        except Exception:
            return False
        else:
            return True

    def bypass_cloudflare(self):
        if self.driver.capabilities["browserVersion"].split(".")[0] < "115":
            return
        logging.info("Bypassing cloudflare")
        time.sleep(1)
        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            try:
                self.driver.find_element(
                    By.ID, "challenge-stage"
                ).click()  # make sure the challenge is focused
                self.driver.execute_script(
                    '''window.open("''' + self.driver.current_url + """","_blank");"""
                )  # open page in new tab
                input(
                    "\033[93mWarning: Bypassing Cloudflare\nplease click on the captcha checkbox if not done already "
                    "and press enter to continue (do not close any of the tabs)\033[0m"
                )
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch to first tab
                self.driver.close()  # close first tab
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch back to new tab
            except Exception as e:
                logging.error("Could not bypass cloudflare: " + str(e))
                return
        else:
            logging.info("No need to bypass cloudflare")
            return

    def run(self, course_url, email, password, login_url, man_login_url):
        logging.info("Starting login")

        if man_login_url is None:
            # Check if login_url is not set
            if login_url is None:
                try:
                    self.find_login(course_url)
                except Exception as e:
                    logging.error("Could not find login: " + str(e), exc_info=self.verbose)
            else:
                self.driver.get(login_url)

            try:
                self.login(email, password)
            except Exception as e:
                logging.error("Could not login: " + str(e), exc_info=self.verbose)
                return
        else:
            self.driver.get(course_url)
            while self.driver.current_url != man_login_url:
                time.sleep(3)
                logging.info("Waiting for user to navigate to url: " + man_login_url)
                logging.info("Current url: " + self.driver.current_url)

        logging.info("Starting download of course: " + course_url)
        try:
            self.pick_course_downloader(course_url)
        except Exception as e:
            logging.error("Could not download course: " + course_url + " cause: " + str(e))

    def run_batch(self, url_array, email, password, login_url, man_login_url):
        """
        This method handles batch downloading of courses. It navigates to the given URLs, logs in if necessary,
        and initiates the download process for each course.

        :param url_array: List[str]
            An array of URLs pointing to the courses that need to be downloaded.
        :param email: str
            The email address used to log in to the platform.
        :param password: str
            The password associated with the provided email address.
        :param login_url: str
            The URL of the login page. If not provided, manual login is assumed.
        :param man_login_url: str
            The URL of the page to navigate to after manual login. This parameter is optional.
            If provided, the script will wait until the user has manually navigated to this URL
            before starting the download process.
        :return: None
        """
        logging.info("Starting login")

        if man_login_url is None:
            # Check if login_url is not set
            if login_url is not None:
                self.driver.get(login_url)
            else:
                logging.error("Login url is not set")
                return

            try:
                self.login(email, password)
            except Exception as e:
                logging.error("Could not login: " + str(e), exc_info=self.verbose)
                return
        else:
            self.driver.get(url_array[0])
            while self.driver.current_url != man_login_url:
                time.sleep(3)
                logging.info("Waiting for user to navigate to url: " + man_login_url)
                logging.info("Current url: " + self.driver.current_url)

        logging.info("Running batch download of courses ")
        for url in url_array:
            try:
                self.pick_course_downloader(url)
            except Exception as e:
                logging.error("Could not download course: " + url + " cause: " + str(e))

    def construct_sign_in_url(self, course_url):
        parsed_url = urlparse(course_url)
        # Replace the path with '/sign_in'
        sign_in_path = '/sign_in'
        fallback_url = urlunparse((parsed_url.scheme, parsed_url.netloc, sign_in_path, '', '', ''))
        return fallback_url

    def find_login(self, course_url):
        logging.info("Trying to find login")

        self.driver.implicitly_wait(self.global_timeout)
        self.driver.get(course_url)

        try:
            login_element = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.LINK_TEXT, "Login"))
            )
        except TimeoutException:
            logging.warning("Login button not found, navigating to fallback URL")
            fallback_url = self.construct_sign_in_url(course_url)
            self.driver.get(fallback_url)
        else:
            login_element.click()

    def login(self, email, password):
        logging.info("Logging in")

        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            self.bypass_cloudflare()

        WebDriverWait(self.driver, timeout=5).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        email_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.ID, "email")))
        password_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.ID, "password")))
        commit_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.NAME, "commit")))

        logging.debug("Filling in login form")
        email_element.click()
        email_element.clear()
        self.driver.execute_script("document.getElementById('email').value='" + email + "'")

        password_element.click()
        password_element.clear()
        self.driver.execute_script("document.getElementById('password').value='" + password + "'")

        commit_element.click()

        # Check for login error due to incorrect credentials
        logging.debug("Checking for login error")
        try:
            error_elements = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.toast, span.text-with-icon"))
            )
            for element in error_elements:
                if "Your email or password is incorrect" in element.text:
                    logging.error("Login failed: Incorrect email or password.")
                    return False
        except TimeoutException:
            # No error message found, continue
            pass

        # Check for new device challenge
        # input with name otp_code
        if self.check_elem_exists(By.NAME, "otp_code", timeout=self.global_timeout):
            # wait for user to enter code
            input(
                "\033[93mWarning: New device challenge\nplease enter the code sent to your email and press enter to "
                "continue\033[0m"
            )
        logging.info("Logged in, switching to course page")
        time.sleep(3)

    def pick_course_downloader(self, course_url):
        # Check if we are already on the course page
        if not self.driver.current_url == course_url:
            logging.info("Switching to course page")
            self.driver.get(course_url)
            if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
                self.bypass_cloudflare()

        WebDriverWait(self.driver, timeout=self.global_timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        # https://support.teachable.com/hc/en-us/articles/360058715732-Course-Design-Templates
        logging.info("Picking course downloader")
        if self.driver.find_elements(By.ID, "__next"):
            logging.info('Choosing __next format')
            self.download_course_simple(course_url)
        elif self.driver.find_elements(By.CLASS_NAME, "course-mainbar"):
            logging.info('Choosing course-mainbar format')
            self.download_course_classic(course_url)
        elif self.driver.find_elements(By.CSS_SELECTOR, ".block__curriculum"):
            logging.info('Choosing .block__curriculum format')
            self.download_course_colossal(course_url)
        else:
            logging.error("Downloader does not support this course template. Please open an issue on github.")

    def download_course_colossal(self, course_url):
        logging.info("Detected block course format")
        try:
            logging.info("Getting course title")
            course_title = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".lecture_heading"))
            ).text
        except Exception as e:
            logging.warning("Could not get course title, using tab title instead")
            course_title = self.driver.title

        # course_title = clean_string(course_title)
        course_path = create_folder(course_title)

        logging.info("Saving course html")
        try:
            output_file = os.path.join(course_path, "course.html")
            with open(output_file, 'w+') as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Unhide all elements
        logging.info("Unhiding all elements")
        self.driver.execute_script('[...document.querySelectorAll(".hidden")].map(e=>e.classList.remove("hidden"))')

        chapter_idx = 1
        video_list = []
        sections = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".block__curriculum__section"))
        )

        for section in sections:
            chapter_title = section.find_element(By.CSS_SELECTOR, ".block__curriculum__section__title").text
            chapter_title = clean_string(chapter_title)
            chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            chapter_idx += 1
            idx = 1

            section_items = section.find_elements(By.CSS_SELECTOR, ".block__curriculum__section__list__item__link")
            for section_item in section_items:
                lecture_link = section_item.get_attribute("href")

                lecture_title = section_item.find_element(By.CSS_SELECTOR,
                                                          ".block__curriculum__section__list__item__lecture-name").text
                lecture_title = clean_string(lecture_title)
                lecture_title = ''.join(char for char in lecture_title if char in string.printable)
                logging.info("Found lecture: " + lecture_title)

                truncated_lecture_title = truncate_title_to_fit_file_name(lecture_title)

                video_entity = {"link": lecture_link, "title": truncated_lecture_title, "idx": idx,
                                "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def download_course_classic(self, course_url):
        # self.driver.find_elements(By.CLASS_NAME, "course-mainbar")
        logging.info("Detected _mainbar course format")
        print("Detected _mainbar course format")
        try:
            logging.debug("Getting course title")
            course_title = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body > section > div.course-sidebar > div > h2"))
            ).text
        except Exception as e:
            logging.warning("Could not get course title, using tab title instead")
            course_title = self.driver.title

        logging.debug("Found course title: \"" + course_title + "\" starting cleaning of title string")
        print("Found course title: \"" + course_title + "\" starting cleaning of title string")
        course_title = clean_string(course_title)
        logging.info("Found course title: " + course_title)
        print("Found course title: " + course_title)
        course_path = create_folder(course_title)
        print("course_path: ", course_path)

        try:
            logging.debug("Saving course html")
            output_file = os.path.join(course_path, "course.html")
            with open(output_file, 'w+', encoding="utf-8") as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Get course image

        try:
            image_element = self.driver.find_elements(By.CLASS_NAME, "course-image")
            logging.info("Found course image")
            image_link = image_element[0].get_attribute("src")
            image_link_hd = re.sub(r"/resize=.+?/", "/", image_link)
            # try to download the image using the modified link first
            response = requests.get(image_link_hd)
            if response.ok:
                # save the image to disk
                image_path = os.path.join(course_path, "course-image.jpg")
                with open(image_path, "wb") as f:
                    f.write(response.content)
                logging.info("Image downloaded successfully.")
            else:
                # try to download the image using the original link
                response = requests.get(image_link)
                if response.ok:
                    # save the image to disk
                    image_path = os.path.join(course_path, "course-image.jpg")
                    with open(image_path, "wb") as f:
                        f.write(response.content)
                    logging.info("Image downloaded successfully.")
                else:
                    # print a message indicating that the image download failed
                    logging.warning("Failed to download image.")
        except Exception as e:
            logging.warning("Could not find course image: " + str(e))
            pass

        chapter_idx = 1
        video_list = []
        sections = WebDriverWait(self.driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".course-section"))
        )
        for section in sections:
            chapter_title = section.find_element(By.CSS_SELECTOR, ".section-title").text
            # chapter_title = clean_string(chapter_title)
            chapter_title = chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            chapter_idx += 1
            idx = 1

            section_items = section.find_elements(By.CSS_SELECTOR, ".section-item")
            for section_item in section_items:
                lecture_link = section_item.find_element(By.CLASS_NAME, "item").get_attribute("href")

                lecture_title = section_item.find_element(By.CLASS_NAME, "lecture-name").text
                # lecture_title = clean_string(lecture_title)
                logging.info("Found lecture: " + lecture_title)
                # truncated_lecture_title = truncate_title_to_fit_file_name(lecture_title)

                
                video_entity = {"link": lecture_link, "title": lecture_title, "idx": idx,
                                "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def get_course_title_next(self, course_url):
        if self.driver.current_url != course_url:
            self.driver.get(course_url)

        wrap = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".wrap")))
        heading = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".heading")))
        course_title = heading.text

        course_title = clean_string(course_title)
        return course_title

    def download_course_simple(self, course_url):
        print("download_course_simple")
        self.driver.implicitly_wait(2)
        logging.info("Detected next course format")
        course_title = self.get_course_title_next(course_url)
        logging.info("Found course title: " + course_title)
        course_path = create_folder(course_title)

        output_file = os.path.join(course_path, "course.html")
        try:
            with open(output_file, 'w+', encoding='utf-8') as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Download course image
        try:
            logging.info("Downloading course image")
            image_element = self.driver.find_element(By.XPATH, "//*[@id=\"__next\"]/div/div/div[2]/div/div[1]/img")
            logging.info("Found course image")
            image_link = image_element.get_attribute("src")
            # Save image
            image_path = os.path.join(course_path, "course-image.jpg")
            # send a GET request to the image link
            try:
                response = requests.get(image_link)
                # write the image data to a file
                with open(image_path, "wb") as f:
                    f.write(response.content)
                # print a message indicating that the image was downloaded
                logging.info("Image downloaded successfully.")
            except Exception as e:
                # print a message indicating that the image download failed
                logging.warning("Failed to download image:" + str(e))
        except Exception as e:
            logging.warning("Could not find course image: " + str(e))
            pass

        chapter_idx = 0
        video_list = []
        slim_sections = self.driver.find_elements(By.CSS_SELECTOR, ".slim-section")
        for slim_section in slim_sections:
            chapter_idx += 1
            bars = slim_section.find_elements(By.CSS_SELECTOR, ".bar")
            chapter_title = slim_section.find_element(By.CSS_SELECTOR, ".heading").text
            chapter_title = clean_string(chapter_title)
            chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            try:
                not_available_element = WebDriverWait(slim_section, self.global_timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".drip-tag")))
                logging.warning('Chapter "%s" not available, skipping', chapter_title)
                continue
            except TimeoutException:
                logging.info("Chapter is available")
                pass  # Element wasn't found so the chapter is available

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            idx = 1
            for bar in bars:
                video = bar.find_element(By.CSS_SELECTOR, ".text")
                link = video.get_attribute("href")
                # Remove new line characters from the title and replace spaces with -
                title = clean_string(video.text)
                logging.info("Found lecture: " + title)
                truncated_title = truncate_title_to_fit_file_name(title)
                video_entity = {"link": link, "title": truncated_title, "idx": idx, "download_path": download_path}
                video_list.append(video_entity)
                idx += 1
    
        self.download_videos_from_links(video_list)

    def download_videos_from_links(self, video_list):
        timeout = 15
        for video in video_list:
            print(video["title"])
            if self.driver.current_url != video["link"]:
                logging.info("Navigating to lecture: " + video["title"])
                self.driver.get(video["link"])
                self.driver.implicitly_wait(timeout)
            logging.info("Downloading lecture: " + video["title"])

            # logging.info("Disabling autoplay")
            # self.driver.execute_script('var checkbox = document.getElementById("custom-toggle-autoplay");'
            #                            'if (checkbox.checked) {checkbox.click();}')

            try:
                logging.info("Saving html")
                self.save_webpage_as_html(video["title"], video["idx"], video["download_path"])
            except Exception as e:
                logging.error("Could not save html: " + video["title"] + " cause: " + str(e), exc_info=self.verbose)

            # try:
            #     logging.info("Downloading attachments")
            #     self.download_attachments(video["link"], video["title"], video["idx"], video["download_path"])
            # except Exception as e:
            #     logging.warning("Could not download attachments: " + video["title"] + " cause: " + str(e))
            
            try:
                logging.debug("Trying to download video as an attachment")
                if self.download_video_file(video["title"], video["idx"], video["download_path"]):
                    continue

            except Exception as e:
                logging.debug("Could not download video as an attachment: " + video["title"] + " cause: " + str(e))

            video_iframes = self.driver.find_elements(By.XPATH, "//iframe[starts-with(@data-testid, 'embed-player')]")

            for i, iframe in enumerate(video_iframes):
                try:
                    logging.info("Switching to video frame")
                    self.driver.switch_to.frame(iframe)

                    script_text = self.driver.find_element(By.ID, "__NEXT_DATA__")
                    json_text = json.loads(script_text.get_attribute("innerHTML"))
                    # ["urlEncrypted"] some how cause some 404 here
                    link = json_text["props"]["pageProps"]["applicationData"]["mediaAssets"][0]["url"]
                    # Append -n to the video title if there are multiple iframes
                    video_title = video["title"] + ("-" + str(i + 1) if len(video_iframes) > 1 else "")

                    # try:
                    #     logging.info("Downloading subtitle")
                    #     self.download_subtitle(link, video_title, video["idx"], video["download_path"])
                    # except Exception as e:
                    #     logging.warning("Could not download subtitle: " + video_title + " cause: " + str(e))

                    try:
                        logging.info("Downloading video")
                        self.download_video(link, video_title, video["idx"], video["download_path"])
                    except Exception as e:
                        logging.warning("Could not download video: " + video_title + " cause: " + str(e))

                    self.driver.switch_to.default_content()  # Switch back to main content before the next iteration

                except Exception as e:
                    logging.warning("Could not find video: " + video["title"])
                    continue

            logging.info("Downloaded video: " + video["title"])

            if self._complete_lecture:
                try:
                    logging.info("Completing lecture")
                    self.complete_lecture()
                except Exception as e:
                    logging.warning("Could not complete lecture: " + video["title"] + " cause: " + str(e))

        return

    def complete_lecture(self):
        # Complete lecture
        self.driver.switch_to.default_content()
        complete_button = self.driver.find_element(By.ID, "lecture_complete_button")
        if complete_button:
            logging.info("Found complete button")
            complete_button.click()
            logging.info("Completed lecture")
            time.sleep(3)

    def download_video(self, link, title, video_index, output_path):
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                },
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "http_headers": self.headers,
            "concurrentfragments": 15,
            "outtmpl": os.path.join(output_path, "{:02d}-{}.mp4".format(video_index, title)),
            "verbose": self.verbose,
        }
        print("download_video link: ", link)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # ydl.download([link])
                ydl.download(link)

        except Exception as e:
            logging.error("Could not download video: " + title + " cause: " + str(e))

    # This function is needed because yt-dlp subtitle downloader is not working
    def download_subtitle(self, link, title, video_index, output_path):
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                },
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "http_headers": self.headers,
            "allsubtitles": True,
            "subtitleslangs": ["all"],
            "concurrentfragments": 10,
            "writesubtitles": True,
            "outtmpl": os.path.join(output_path, title),
            "verbose": self.verbose,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(link, download=False)
                info_json = ydl.sanitize_info(info)
        except Exception as e:
            logging.warning("Could not download subtitle: " + title + " cause: " + str(e))

        subtitle_links = {}
        for lang, sub_info in info_json["requested_subtitles"].items():
            subtitle_links[lang] = {"url": sub_info["url"], "ext": sub_info["ext"]}

        # Print the subtitle links and language names
        req = None
        for lang, sub in subtitle_links.items():
            subtitle_filename = "{:02d}-{}.{}.{}".format(video_index, title, lang, sub["ext"])
            file_path = os.path.join(output_path, subtitle_filename)
            if os.path.isfile(file_path):
                logging.info("Skipping existing subtitle: " + subtitle_filename)
            else:
                base_url = sub["url"]
                try:
                    req = requests.get(sub["url"], headers=self.headers)
                except Exception as e:
                    logging.warning("Could not download subtitle: " + title + " cause: " + str(e))
                relative_path = req.text.split("\n")[5]
                full_url = urljoin(base_url, relative_path)
                try:
                    response = requests.get(full_url, headers=self.headers)
                    with open(file_path, "wb") as f:
                        f.write(response.content)
                except Exception as e:
                    logging.warning("Could not download subtitle: " + title + " cause: " + str(e))
                logging.info("Downloaded subtitle: " + subtitle_filename)
                
    def download_video_file(self, title, video_index, output_path, timeout=-1):
        # video_title = "{:02d}-{}".format(video_index, title)
        video_title = video_index, title
        # Grab the video attachments type video
        video_attachment = self.driver.find_element(By.CLASS_NAME, "lecture-attachment-type-video")
        if not video_attachment:
            logging.debug(f"No video attachment found for lecture: {title}")
            return False

        video_link = video_attachment.find_element(By.TAG_NAME, "a")
        print(video_link)
        if not video_link:
            logging.debug(f"No video link found for lecture: {title}")
            return False

        # Set the download directory for this file
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": output_path
        })
        # Get list of files before download
        files_before_download = set(os.listdir(output_path))

        # Click the link to trigger download
        video_link.click()

        # Wait for download to complete
        start_time = time.time()
        while True:
            files_after_download = set(os.listdir(output_path))

            # Find new files
            new_files = files_after_download - files_before_download

            if len(new_files) == 1 and not list(new_files)[0].endswith('.crdownload'):
                break
            
            if timeout > 0 and (time.time() - start_time) > timeout:
                logging.warning(f"Download timeout for lecture: {title}")
                return False
        
            time.sleep(1)

        latest_file = os.path.join(output_path, list(new_files)[0])
                
        # Determine the file extension
        _, extension = os.path.splitext(latest_file)
        
        # Create the new filename
        new_filename = f"{video_title}{extension}"
        new_filepath = os.path.join(output_path, new_filename)
        
        # Rename the file
        os.rename(latest_file, new_filepath)
        logging.info(f"Downloaded video file {new_filename}")
        return True
    
    def download_attachments(self, link, title, video_index, output_path):
        video_title = "{:02d}-{}".format(video_index, title)

        # Grab the video attachments type file
        video_attachments = self.driver.find_elements(By.CLASS_NAME, "lecture-attachment-type-file")
        # Get all links from the video attachments

        if video_attachments:
            video_links = video_attachments[0].find_elements(By.TAG_NAME, "a")

            output_path = os.path.join(output_path, video_title)
            os.makedirs(output_path, exist_ok=True)

            # Get href attribute from the first link
            if video_links:
                for video_link in video_links:
                    link = video_link.get_attribute("href")
                    file_name = video_link.text
                    logging.info("Downloading attachment: " + file_name + " for video: " + title)
                    # Download file and save the file in output_path directory
                    wget.download(link, out=output_path)
        else:
            logging.warning("No attachments found for video: " + title)

    def save_webpage_as_html(self, title, video_index, output_path):
        output_file = os.path.join(output_path, "{:02d}-{}.html".format(video_index, title))
        with open(output_file, 'w+', encoding='utf-8') as f:
            f.write(self.driver.page_source)
        logging.info("Saved webpage as html: " + output_file)

    def save_webpage_as_pdf(self, title, video_index, output_path):
        output_file_pdf = os.path.join(output_path, "{:02d}-{}.pdf".format(video_index, title))
        self.driver.save_print_page(output_file_pdf)
        logging.info("Saved webpage as pdf: " + output_file_pdf)

    def clean_up(self):
        logging.info("Cleaning up")
        self.driver.quit()
        # Delete cookies.txt
        if os.path.exists("cookies.txt"):
            os.remove("cookies.txt")


def read_urls_from_file(file_path):
    urls = []
    try:
        with open(file_path, 'r') as file:
            urls = file.read().splitlines()
    except FileNotFoundError:
        logging.error(f"File not found: {file_path}")
    except IOError as e:
        logging.error(f"IOError reading file: {file_path}. Error: {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error reading file: {file_path}. Error: {str(e)}")

    if urls:
        logging.info(f"Successfully read {len(urls)} URLs from file: {file_path}")
    else:
        logging.warning(f"No URLs found in file: {file_path}")

    return urls


def check_required_args(args):
    if args.email and args.password:
        return True
    elif args.man_login_url:
        return True
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Teachable-Dl', description='Download courses', )
    parser.add_argument("--url", required=False, help='URL of the course', default=URL)
    parser.add_argument("-e", "--email", required=False, help='Email of the account', default=EMAIL)
    parser.add_argument("-p", "--password", required=False, help='Password of the account', default=PASSWORD)
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity level (repeat for more verbosity)')
    parser.add_argument('--complete-lecture', action='store_true', default=False,
                        help='Complete the lecture after downloading')
    parser.add_argument("--login_url", required=False, help='(Optional) URL to teachable SSO login page')
    parser.add_argument("--man_login_url", required=False,
                        help='Login manually and start downloading when this url is reached')
    parser.add_argument("-f", "--file", required=False, help='Path to a text file that contains URLs')
    parser.add_argument("--user-agent", required=False, help='User agent to use when downloading videos',
                        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/116.0.0.0 Safari/537.36")
    parser.add_argument("-t", "--timeout", required=False, help='Timeout for selenium driver', default=10)
    args = parser.parse_args()
    verbose = False
    if args.verbose == 0:
        log_level = logging.WARNING
    elif args.verbose == 1:
        log_level = logging.INFO
    else:
        verbose = True
        log_level = logging.DEBUG

    logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')

    if not check_required_args(args):
        logging.error("Required arguments are missing. Choose email/password or manual login (man_login_url).")
        exit(1)

    downloader = TeachableDownloader(verbose_arg=verbose, complete_lecture_arg=args.complete_lecture,
                                     user_agent_arg=args.user_agent, timeout_arg=args.timeout)
    if args.file:
        urls = read_urls_from_file(args.file)
        try:
            downloader.run_batch(urls, args.email, args.password, args.login_url, args.man_login_url)
            downloader.clean_up()
            sys.exit(0)
        except KeyboardInterrupt:
            logging.error("Interrupted by user")
            downloader.clean_up()
            sys.exit(1)
        except Exception as e:
            logging.error("Error: " + str(e))
            downloader.clean_up()
            sys.exit(1)
    else:
        # Check if url argument is passed
        if not args.url:
            logging.error("URL is required")
            sys.exit(1)
        try:
            downloader.run(course_url=args.url, email=args.email, password=args.password, login_url=args.login_url,
                           man_login_url=args.man_login_url)
            downloader.clean_up()
            sys.exit(0)
        except KeyboardInterrupt:
            logging.error("Interrupted by user")
            downloader.clean_up()
            sys.exit(1)
        except Exception as e:
            logging.error("Error: " + str(e))
            downloader.clean_up()
            sys.exit(1)
