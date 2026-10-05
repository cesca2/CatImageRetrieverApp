import csv
import re
import sys
from pathlib import Path

import requests
from pathvalidate import (
    is_valid_filename,
    is_valid_filepath,
    sanitize_filename,
    sanitize_filepath,
)
from PIL import Image, UnidentifiedImageError
from rich import print as rich_print
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

APP_INPUTTEXT_SUPPORTED_REGEX = r"^[a-zA-Z0-9  !-'#_]+$"
APP_INPUTTEXT_UNSUPPORTED_CHARS_REGEX = r"[^a-zA-Z0-9  !-'#_]"

APP_INPUTTEXT_CHARLIMIT = 40

# keywords in API parameters used as keys in text_options
APP_ADDTEXT_OPTIONS = (
    "text",
    "fontColor",
    "fontSize",
    "font",
)
APP_ADDTEXT_TYPES = {"text": str, "fontColor": str, "fontSize": float, "font": str}
# initial default values for text
APP_ADDTEXT_DEFAULTS = {
    "text": "Miaow",
    "fontColor": "white",
    "fontSize": 50.0,
    "font": "Impact",
}
# restrict choices presented to user for some text formatting options
APP_ADDTEXT_OPTIONS_CHOICES = {
    "font": [
        "Andale Mono",
        "Impact",
        "Arial",
        # "Arial Black",
        "Comic Sans MS",
        "Courier New",
        "Georgia",
        "Times New Roman",
        "Verdana",
        # "Webdings",
    ],
    "fontColor": ["black", "red", "white", "green", "purple", "pink"],
}
# assertion statements to sense check global consts used in app configuration -- consts only ever touched by developer
assert all(
    i in APP_ADDTEXT_TYPES for i in APP_ADDTEXT_OPTIONS
), "APP_ADDTEXT_OPTIONS must all be registered with their type in APP_ADDTEXT_TYPES"
assert all(
    i in APP_ADDTEXT_DEFAULTS for i in APP_ADDTEXT_OPTIONS
), "APP_ADDTEXT_OPTIONS must all be registered with a default value in APP_ADDTEXT_DEFAULTS"
for option in APP_ADDTEXT_OPTIONS:
    assert APP_ADDTEXT_TYPES[option] == type(
        APP_ADDTEXT_DEFAULTS[option]
    ), f"APP_ADDTEXT_DEFAULTS does not contain expected types for all choices available for {option}"
for key, choices in APP_ADDTEXT_OPTIONS_CHOICES.items():
    assert all(
        type(i) == APP_ADDTEXT_TYPES[key] for i in choices
    ), f"APP_ADDTEXT_OPTIONS_CHOICES does not contain expected types for {key}"


class InvalidInputTextLength(Exception):
    pass


class InvalidInputTextCharactersException(Exception):
    pass


def image_file_options(
    image_json: dict,
    mode: str = "preview",
):
    try:
        im = Image.open(
            requests.get(image_json["url"], stream=True, timeout=10).raw
        )  # allows to open image directly from requests see https://github.com/python-pillow/Pillow/pull/1151
    except UnidentifiedImageError:
        rich_print(
            "[red]Image could not be opened, sorry![/red] Try again or return to Main Menu and request a new image\n"
        )
        return
    except (
        requests.exceptions.HTTPError,
        requests.exceptions.JSONDecodeError,
        requests.exceptions.Timeout,
        requests.exceptions.TooManyRedirects,
    ):
        rich_print(
            "[red]Image could not be opened, sorry (issue reading from the API)![/red] Try again or return to Main Menu and request a new image\n"
        )
        return
    if mode == "preview":
        try:
            im.show()
            rich_print(
                "[green]Image preview opened[/green]\n[gold3]On mac[/gold3]: see your Preview application\n[gold3]On Windows[/gold3]: see your PNG display utility\n[gold3]On Unix[/gold3]: see one of xdg-open, display, gm, eog, or xv utilities (dependent on installation)\n"  # see https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.show
            )
        except:
            rich_print(
                "[red]Image preview not currently working, please try another option[/red]"  # unsure without system testing/extensive review which kind of exception would be raised here
            )

    elif mode == "save":
        default_dir = "images"

        # lookup type of image from json info mimetype one of: image/jpeg, image/png, image/gif
        image_ext = image_json["mimetype"].split("/")[1]
        image_format = image_ext.upper()
        ext_lookup = {"png": ".png", "jpeg": ".jpg", "gif": ".gif"}

        default_file_name = sanitize_filename(
            "_".join(image_json["tags"] + ["cat", image_json["id"]])
            + ext_lookup[image_ext]
        )  # sanitize default filepath straight away

        while True:
            confirm_defaults = Confirm.ask(
                f"Would you like to customise the save location [cyan]({Path(default_dir, default_file_name)})[/cyan]?",
                default=False,
            )
            while True:
                file_directory = (
                    default_dir
                    if not confirm_defaults
                    else Prompt.ask("Enter folder name choice")
                )
                check, suggestion = check_path(file_directory)
                if check:
                    break

                elif Confirm.ask(
                    f"[red]{file_directory} is not a valid directory[/red], is {suggestion} suitable instead?"
                ):
                    file_directory = suggestion
                    break

            Path.mkdir(Path(file_directory), parents=True, exist_ok=True)

            while True:
                file_name = (
                    default_file_name
                    if not confirm_defaults
                    else Prompt.ask("Enter file name choice (without extension)")
                    + ext_lookup[image_ext]
                )
                check, suggestion = check_path(file_name, filename=True)
                if check:
                    break

                elif Confirm.ask(
                    f"[red]{file_name.removesuffix(ext_lookup[image_ext])} is not a valid filename[/red], is {suggestion.removesuffix(ext_lookup[image_ext])} suitable instead?"
                ):
                    file_name = suggestion
                    break

            image_path = Path(file_directory, file_name)

            if image_path.exists():
                confirm_overwrite = Confirm.ask("Overwrite existing file? ")

                if confirm_overwrite:
                    break
                else:
                    continue
            else:
                break

        if image_format == "GIF":
            im.save(
                image_path, save_all=True, format=image_format
            )  # https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#gif-saving save_all=True to preserve gif
        else:
            im.save(image_path, format=image_format)

        rich_print(f"[green]Image saved to {image_path}[/green]\n")

    else:
        print("Specify either 'preview' or 'save' as mode")


def get_json_cataas_api(
    id: str = "",
    resource: str = "cat",
    tags: list | None = None,
    text_options: dict | None = None,
) -> dict | None:

    base_url = f"https://cataas.com/{resource}"

    if id:
        base_url += f"/{id}"
    elif tags is not None and tags:
        base_url += "/" + ",".join(tags)

    request_params = {"json": "true"}
    if text_options is not None:
        base_url += f"/says/{text_options["text"]}"
        for key, value in text_options.items():
            request_params[key] = value

    try:
        response = requests.get(base_url, params=request_params, timeout=10)
        response.raise_for_status()
        response_json = response.json()  # check for JSON decode error here
    except (
        requests.exceptions.HTTPError,
        requests.exceptions.Timeout,
        requests.exceptions.JSONDecodeError,
        requests.exceptions.TooManyRedirects,
    ):
        return  # json = None indicates something went wrong

    write_requests_log(response_json, text_options)

    return response_json


def write_requests_log(
    json: dict,
    text_options: dict | None,
):
    all_keys = list(
        tuple(json.keys()) + APP_ADDTEXT_OPTIONS
    )  # use global text options to get all possible fields when creating csv records file

    fieldnames = [
        x[4::] if x == "mimetype" else x for x in all_keys
    ]  # use string slicing to rename mimetype -> type

    records_path = Path("app_records/image_requests_log.csv")
    if not records_path.exists():
        Path.mkdir(records_path.parent, exist_ok=True, parents=True)
        with open(
            records_path, "w", newline=""
        ) as csvfile:  # newline = "" recommended see https://docs.python.org/3/library/csv.html#id4
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

    csv_dict = {}
    for key, value in json.items():
        if key != "mimetype":
            csv_dict[key] = value
        else:
            csv_dict[key[4::]] = value
    if text_options is not None:
        for key, value in text_options.items():
            csv_dict[key] = value

    with open(records_path, "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writerow(csv_dict)

    rich_print(
        f"[green]Request details written successfully to {records_path}[/green]\n"
    )


def edit_text_options(text_options: dict | None = None) -> dict:

    if text_options is None:
        # initial default values
        text_options = APP_ADDTEXT_DEFAULTS

    # text options is still controlled by app logic rather than provided directly from user
    assert len(text_options.keys()) == len(
        APP_ADDTEXT_OPTIONS
    ), "Mismatch between global app settings available text options and those used in this function"

    display_name_text_options = {
        "text": "Text",
        "fontColor": "Text Colour",
        "fontSize": "Text Size",
        "font": "Text Font",
    }
    number_choice_options = {}

    while True:
        # adapted from simple example in https://rich.readthedocs.io/en/stable/tables.html
        table = Table(
            title="\nCurrent Image Text Options\n",
            caption="\nChoose a numbered option below to edit text settings \nOR\nPress enter to confirm current settings and continue",
            show_lines=True,
            title_style="cornflower_blue",
            caption_style="gold3",
        )
        table.add_column("", justify="right", style="cyan")
        table.add_column("Option", justify="right", style="dark_orange3")
        table.add_column("Value", justify="right", style="dark_magenta")

        for i, item in enumerate(text_options.items(), start=1):
            table.add_row(
                f"[{i}]",
                display_name_text_options[item[0]],
                str(item[1]),
            )
            number_choice_options[i] = item[0]
        console = Console()
        console.print(table)

        selection = IntPrompt.ask(
            "\nEnter selection",
            choices=[str(key) for key in number_choice_options],
            default="Continue",  # this lets user just press enter to continue
            show_choices=True,
            show_default=True,
        )
        if selection == "Continue":
            return text_options
        else:
            text_option = number_choice_options[selection]
            prompt_choices = APP_ADDTEXT_OPTIONS_CHOICES.get(
                text_option, None
            )  # not every option has restricted list of choices

            if APP_ADDTEXT_TYPES[text_option] == int:
                new_value = IntPrompt.ask(
                    f"Please choose a new {display_name_text_options[text_option]} [magenta][Must be an integer][/magenta]",
                    choices=prompt_choices,
                )
            elif APP_ADDTEXT_TYPES[text_option] == float:
                new_value = FloatPrompt.ask(
                    f"Please choose a new {display_name_text_options[text_option]} [magenta][Must be a float][/magenta]",
                    choices=prompt_choices,
                )
            else:
                while True:
                    new_value = Prompt.ask(
                        f"Please choose a new {display_name_text_options[text_option]}",
                        choices=prompt_choices,
                    )
                    check, message = check_input_text(
                        new_value
                    )  # Prompt.ask very permissive so run extra check to sanitize user input
                    if check:
                        break
                    else:
                        rich_print(f"[prompt.invalid]{message}")
                        continue

            text_options[text_option] = new_value


def check_input_text(text: str) -> tuple[bool, str]:
    try:
        if len(text) > APP_INPUTTEXT_CHARLIMIT or len(text) == 0:
            raise InvalidInputTextLength("Length of text is invalid")
        elif not re.match(APP_INPUTTEXT_SUPPORTED_REGEX, text):
            raise InvalidInputTextCharactersException("Text characters not supported")

    except InvalidInputTextLength:
        if text:
            validation_message = f"Please adjust your entry. Text exceeds character limit (input is currently {len(text)} characters, limit is {APP_INPUTTEXT_CHARLIMIT})"
        else:
            validation_message = "Please enter text (length of input is currently 0)"
        return False, validation_message
    except InvalidInputTextCharactersException:

        validation_message = f"Please adjust characters used, unsupported characters were included: {" and ".join(set(re.findall(APP_INPUTTEXT_UNSUPPORTED_CHARS_REGEX, text)))}"

        return False, validation_message
    else:
        return True, ""


def check_tags(tags: list[str]) -> bool:

    retries = 0
    try:
        response = requests.get(
            f"https://cataas.com/cat/{",".join(tags)}?json=true", timeout=10
        )  # timeout recommended for production code https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts
        response.raise_for_status()
        response = response.json()

    except (
        requests.exceptions.HTTPError,
        requests.exceptions.JSONDecodeError,
        requests.exceptions.Timeout,
        requests.exceptions.TooManyRedirects,
    ):
        retries += 1
        if (
            response.status_code == "404" or retries > 3
        ):  # no matches to tags return 404 "Cat Not Found"
            return False
    else:
        return True


def check_path(input: str, filename: bool = False) -> tuple[bool, str]:
    if filename:
        if is_valid_filename(input):
            return True, ""
        else:
            return False, sanitize_filename(input)
    else:
        if is_valid_filepath(input):
            return True, ""
        else:
            return False, sanitize_filepath(input)


def add_tag_menu() -> list[str]:
    tags = []
    loop_count = 0
    while True:

        rich_print(
            Panel.fit(
                f"[gold3]Which tags would you like to include?[/gold3]\n[cornflower_blue]Current tags {tags}",
                border_style="red",
                title=":label:  Tag Options Menu :label:",
                padding=2,
            )
        )

        # don't display the confirm finish prompt on first iteration of loop
        if loop_count > 0:
            confirm = Confirm.ask("Are you finished adding tags?")
            if confirm:
                return tags

        loop_count += 1
        while True:
            prompt_tag = Prompt.ask("Add tag")
            check, message = check_input_text(prompt_tag)
            if check:
                break
            else:
                rich_print(f"[prompt.invalid]{message}")
                continue
        if check_tags(tags + [prompt_tag]):
            tags.append(prompt_tag)
        else:
            rich_print(
                "[red]No images found with the requested tag, please try another tag or continue[/red]\n"
            )


def application_ui():
    image_tags = None
    image_text_options = None

    rich_print(
        Panel.fit(
            "[gold3]Which type of cat image would you like?[/gold3]\n\n1. Random :game_die:\n2. Search by keyword tags :mag:\n3. Exit application :wave::door:",
            border_style="red",
            title=":cat: Main Menu :cat:",
            padding=2,
        )
    )
    main_selection = IntPrompt.ask("", choices=["1", "2", "3"], show_choices=False)

    if main_selection == 3:
        sys.exit()

    elif main_selection == 2:
        image_tags = add_tag_menu()

    image_json = None
    image_id = ""
    while True:
        retries = 0
        while image_json is None and retries < 3:
            if retries > 0:
                print("\nRetrying...")
            print("\nFetching your image...")

            image_json = get_json_cataas_api(
                id=image_id,
                tags=image_tags,
                text_options=image_text_options,
            )
            if image_json:
                break
            else:
                retries += 1
        if image_json is None:
            rich_print(
                "[red]Error obtaining your image could not be resolved, please try again later![/red]\n"
            )
            application_ui()

        rich_print(
            Panel.fit(
                "[gold3]What would you like to do with your image now?[/gold3]\n\n1. Preview image :cat::framed_picture: \n2. Add (or edit) image text :speech_balloon: \n3. Save image :open_file_folder:\n4. Return to Main Menu :house: [red]:exclamation:Remember to save first:exclamation:[/red]",
                border_style="red",
                title=":camera: :cat2: Image Options Menu :cat2: :camera:",
                padding=2,
            )
        )

        final_selection = IntPrompt.ask(
            "", choices=["1", "2", "3", "4"], show_choices=False
        )

        if final_selection == 4:
            application_ui()  # breaks loop and returns to main menu
        elif final_selection == 3:
            image_file_options(image_json, mode="save")
        elif final_selection == 1:
            image_file_options(image_json, mode="preview")
        elif final_selection == 2:
            image_text_options = edit_text_options(image_text_options)
            image_id = image_json[
                "id"
            ]  # pass id as argument in next loop iteration to return same image as before just with additional text
            image_json = None


if __name__ == "__main__":
    while True:
        try:
            application_ui()
        except (
            requests.exceptions.ConnectionError
        ):  # if ever network connection failure with requests during app excecution return user to main menu safely
            rich_print(
                "[red]Could not connect to the API to continue processing your request, check your network connection and try again later[/red]\n"
            )
        else:
            break  # break loop if application UI doesn't raise any handled exception
