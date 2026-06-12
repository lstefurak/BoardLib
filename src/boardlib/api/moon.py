import datetime

import bs4
import requests

import boardlib.util.grades

HOST = "https://moonboard.com"

# The Moonboard API always returns English month abbreviations; strptime's %b
# is locale-dependent, so parse the month explicitly.
MONTH_ABBREVIATIONS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_climbed_date(date_string):
    """Parse a "05 Sep 2023" style date independent of the active locale."""
    day, month_name, year = date_string.strip().split()
    return datetime.date(int(year), MONTH_ABBREVIATIONS[month_name.lower()[:3]], int(day))

BOARD_IDS = {
    "moon2016": 1,
    "moon2017": 15,
    "moon2019": 17,
    "moon2020": 19,
    "moon2024": 21,
}

ANGLES_TO_IDS = {
    "moon2016": {
        40: 3,
    },
    "moon2017": {
        25: 2,
        40: 1,
    },
    "moon2019": {
        25: 2,
        40: 1,
    },
    "moon2020": {
        40: 1,
    },
    "moon2024": {
        25: 2,
        40: 3,
    },
}

ATTEMPTS_TO_COUNT = {
    "Flashed": "1",
    "2nd try": "2",
    "3rd try": "3",
    "more than 3 tries": "4+",
    "Project": "project"
}

IDS_TO_ANGLES = {
    board_name: {angle_id: angle for angle, angle_id in angle_map.items()}
    for board_name, angle_map in ANGLES_TO_IDS.items()
}


def get_session(username, password):
    session = requests.Session()
    session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
})
    login_page = session.get(f"{HOST}/account/login")
    login_page.raise_for_status()
    
    loging_page_soup = bs4.BeautifulSoup(login_page.text, "html.parser")
    form = loging_page_soup.find("form", {"id": "frmLogin"})
    verification_token = form.find("input", {"name": "__RequestVerificationToken"})["value"]
    form_key = form.find("input", {"name": "form_key"})["value"]
    
    login_response = session.post(
        f"{HOST}/Account/login",
        data={
            "Login.Username": username,
            "Login.Password": password,
            "__RequestVerificationToken": verification_token,
            "form_key": form_key,
        },
        headers={
        'Referer': f'{HOST}/account/login',
        'Content-Type': 'application/x-www-form-urlencoded',
        }
    )
    login_response.raise_for_status()
    
    # Check if login was successful by looking for error messages. Any error
    # shown on the login response (wrong credentials, locked account, too many
    # attempts, ...) means the session is not authenticated; returning it would
    # only fail later with an opaque JSON decode error.
    login_response_soup = bs4.BeautifulSoup(login_response.text, "html.parser")
    error_selectors = ('.validation-summary-errors', '.field-validation-error', '.text-danger', '.alert-danger')
    for selector in error_selectors:
        for element in login_response_soup.select(selector):
            error_text = element.get_text().strip()
            if error_text:
                raise ValueError(f"Login failed: {error_text}")

    return session


def _paged_post(session, url, board, page_size, page=1):
    """Yield "Data" items from a paged Moonboard endpoint until exhausted."""
    while True:
        response = session.post(
            url,
            data={
                "sort": "",
                "page": page,
                "pageSize": page_size,
                "group": "",
                "filter": f"setupId~eq~'{BOARD_IDS[board]}'",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        response_json = response.json()
        yield from response_json["Data"]
        if response_json["Total"] <= page_size * page:
            return
        page += 1


def logbook_pages(session, board, page_size=40, page=1):
    yield from _paged_post(session, f"{HOST}/Logbook/GetLogbook", board, page_size, page)


def raw_logbook_entries_for_page(session, board, entry_id, page_size=30, page=1):
    yield from _paged_post(
        session, f"{HOST}/Logbook/GetLogbookEntries/{entry_id}", board, page_size, page
    )


def raw_logbook_entries(session, board, logbook_page_size=40, entry_page_size=30):
    logbook = logbook_pages(session, board, page_size=logbook_page_size)
    for entry in logbook:
        yield from raw_logbook_entries_for_page(
            session, board, entry["Id"], page_size=entry_page_size
        )


def get_my_ranking(session, board, angle):
    response = session.post(
        f"{HOST}/Dashboard/GetMyRanking/{BOARD_IDS[board]}/{ANGLES_TO_IDS[board][angle]}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    response.raise_for_status()
    return response.json()


def get_summary_by_benchmark_tries(session, board, angle):
    response = session.post(
        f"{HOST}/Dashboard/GetSummaryByBenchmarkTries/{BOARD_IDS[board]}/{ANGLES_TO_IDS[board][angle]}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    response.raise_for_status()
    return response.json()


def logbook_entries(board, username, password, grade_type="font"):
    session = get_session(username, password)
    entries = raw_logbook_entries(session, board)
    for entry in entries:
        problem = entry["Problem"]
        font_logged_grade = problem["UserGrade"]
        font_displayed_grade = problem.get("Grade", font_logged_grade)
        yield {
            "board": board,
            "angle": IDS_TO_ANGLES[board][
                problem["MoonBoardConfiguration"]["Id"]
            ],
            "climb_name": problem["Name"],
            "date": parse_climbed_date(entry["DateClimbedAsString"]).isoformat(),
            # .get() so an ungraded entry (None) or an unmapped Font grade
            # yields None instead of killing the whole export with a KeyError.
            "displayed_grade": (
                font_displayed_grade
                if grade_type == "font"
                else boardlib.util.grades.FONT_TO_HUECO.get(font_displayed_grade)
            ),
            "logged_grade": (
                font_logged_grade
                if grade_type == "font"
                else boardlib.util.grades.FONT_TO_HUECO.get(font_logged_grade)
            ),
            "is_benchmark": problem.get("IsBenchmark", False),
            "tries": ATTEMPTS_TO_COUNT[entry["NumberOfTries"]],
            "is_mirror" : False,
            "comment": entry.get("Comment")
        }


def get_map_markers(session):
    """
    :return: list of board objects. For example:
        {
            "Name": "The School Room",
            "Description": "The original MoonBoard in the legendary School Room. \r\n\r\nThe School Room, Sheffield is a high spec members-only training facility for intermediate to advanced climbers aged 18+ who wish to improve their strength and endurance for climbing. \r\n\r\nOpen 24/7, 7-days a week.",
            "Image": "/Content/Account/Users/MoonBoards/SchoolRoom.jpg",
            "Latitude": 53.386304,
            "Longitude": -1.47619,
            "IsCommercial": true,
            "IsLed": true,
            "LatLng": [53.386304, -1.47619]
        }
    """
    response = session.get(
        f"{HOST}/MoonBoard/GetMapMarkers",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    response.raise_for_status()
    return response.json()


def gym_boards(session):
    for marker in get_map_markers(session):
        if marker["IsCommercial"]:
            yield {
                "name": marker["Name"],
                "latitude": marker["Latitude"],
                "longitude": marker["Longitude"],
            }
