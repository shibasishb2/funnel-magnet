from streamlit.testing.v1 import AppTest


def test_app_loads_without_errors():
    app = AppTest.from_file("../app.py").run()

    assert not app.exception
    assert app.title[0].value == "Funnel Magnet"
