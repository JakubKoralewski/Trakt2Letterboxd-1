""" Trakt2Letterboxd """

from urllib.request import Request, urlopen, HTTPError
import aiohttp
import asyncio
import json
import time
import csv
import os.path
import webbrowser
import sys, select

class TraktImporter(object):
    """ Trakt Importer """

    def __init__(self):
        self.api_root = 'https://api.trakt.tv'
        self.api_clid = 'b04da548cc9df60510eac7ec1845ab98cebd8008a9978804a981bff7e73ab270'
        self.api_clsc = 'a880315fba01a5e5f0ad7de12b7872e36826a9359b2f419122a24dee1b2cb600'
        self.api_token = None

    def authenticate(self):
        """ Authenticates the user and grabs an API access token if none is available. """

        if self.__decache_token():
            return True

        dev_code_details = self.__generate_device_code()

        self.__show_auth_instructions(dev_code_details)

        got_token = self.__poll_for_auth(dev_code_details['device_code'],
                                         dev_code_details['interval'],
                                         dev_code_details['expires_in'] + time.time())

        if got_token:
            self.__encache_token()
            return True

        return False

    def __decache_token(self):
        if not os.path.isfile("t_token"):
            return False

        token_file = open("t_token", 'r')
        self.api_token = token_file.read()
        token_file.close()
        return True

    def __encache_token(self):
        token_file = open("t_token", 'w')
        token_file.write(self.api_token)
        token_file.close()

    @staticmethod
    def __delete_token_cache():
        os.remove("t_token")

    def __generate_device_code(self):
        """ Generates a device code for authentication within Trakt. """

        request_body = """{{"client_id": "{0}"}}""".format(self.api_clid)
        request_body = request_body.encode('utf-8')
        request_headers = {'Content-Type': 'application/json'}
        request = Request(self.api_root + '/oauth/device/code',
                          data=request_body,
                          headers=request_headers)

        response_body = urlopen(request).read()
        return json.loads(response_body)

    @staticmethod
    def __show_auth_instructions(details):
        url = details['verification_url']
        print(f"\nGo to {url} on your web browser and enter the below user code there:\n\n"
             f"{details['user_code']}\n\nAfter you have authenticated and given permission;"
             "come back here to continue.\n")
        stdin = input("Open browser? (y/N)")
        if stdin.strip().lower() == "y":
            print("Opening browser")
            webbrowser.open(url)
        else:
            print("Waiting for you to authenticate")

    def __poll_for_auth(self, device_code, interval, expiry):
        """ Polls for authorization token """

        request_headers = {'Content-Type': 'application/json'}

        request_body = """{{ "code":          "{0}",
                             "client_id":     "{1}",
                             "client_secret": "{2}" }}
                       """.format(device_code, self.api_clid, self.api_clsc)
        request_body = request_body.encode('utf-8')
        request = Request(self.api_root + '/oauth/device/token',
                          data=request_body,
                          headers=request_headers)

        response_body = ""
        should_stop = False

        print("Waiting for authorization.", end=',')

        while not should_stop:
            time.sleep(interval)

            try:
                response_body = urlopen(request).read()
                should_stop = True
            except HTTPError as err:
                if err.code == 400:
                    print(".", end=',')
                else:
                    print("\n{0} : Authorization failed, please try again. Script will now quit.".format(err.code))
                    should_stop = True

            should_stop = should_stop or (time.time() > expiry)

        if response_body:
            response_dict = json.loads(response_body)
            if response_dict and 'access_token' in response_dict:
                print("Authenticated!")
                self.api_token = response_dict['access_token']
                print("Token:" + self.api_token)
                return True

        # Errored.
        return False

    async def __get_movie(self, session: aiohttp.ClientSession, url: str, page: int, **kwargs):
        response = await session.get(url, **kwargs)
        pages = response.headers["X-Pagination-Page-Count"]
        print(f"Completed {page} of {pages}")
        return await response.json(), pages 

    async def get_movie_list(self, list_name):
        """ Get movie list of the user. """
        print("\nGetting " + list_name)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + self.api_token,
            'trakt-api-version': '2',
            'trakt-api-key': self.api_clid
        }

        extracted_movies = []
        try:
            async with aiohttp.ClientSession() as session:
                json, pages = await self.__get_movie(session, f"{self.api_root}/sync/{list_name}/movies?page={1}&limit=10", 1,
                                headers=headers)
                tasks = []
                for page in range(2, int(pages)+1):
                    tasks.append(self.__get_movie(session, f"{self.api_root}/sync/{list_name}/movies?page={page}&limit=10", page,
                                headers=headers))
                for result in asyncio.as_completed(tasks):
                    json, _pages = await result
                    extracted_movies.extend(
                        self.__extract_fields(json)
                    )
        except HTTPError as err:
            if err.code == 401 or err.code == 403:
                print("Auth Token has expired.")
                # This will regenerate token on next run.
                self.__delete_token_cache()
            print("{0} An error occured. Please re-run the script".format(err.code))
            quit()

        return extracted_movies

    @staticmethod
    def __extract_fields(movies):
        return [{
            'WatchedDate': x['watched_at'] if ('watched_at' in x) else '',
            'tmdbID': x['movie']['ids']['tmdb'],
            'imdbID': x['movie']['ids']['imdb'],
            'Title': x['movie']['title'].encode('utf8'),
            'Year': x['movie']['year'],
        } for x in movies]


def write_csv(history, filename):
    """ Write Letterboxd format CSV """
    if history:
        with open(filename, 'w') as fil:
            writer = csv.DictWriter(fil, history[0].keys())
            writer.writeheader()
            writer.writerows(history)
        return True

    return False


async def run():
    """Get set go!"""

    print("Initializing...")

    importer = TraktImporter()
    if importer.authenticate():
        history = await importer.get_movie_list('history')
        watchlist = await importer.get_movie_list('watchlist')
        if write_csv(history, "trakt-exported-history.csv"):
            print("\nYour history has been exported and saved to the file 'trakt-exported-history.csv'.")
        else:
            print("\nEmpty results, nothing to generate.")

        if write_csv(watchlist, "trakt-exported-watchlist.csv"):
            print("\nYour watchlist has been exported and saved to the file 'trakt-exported-watchlist.csv'.")
        else:
            print("\nEmpty results, nothing to generate.")


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run())
