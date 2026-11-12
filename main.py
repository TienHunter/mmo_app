from multiprocessing import freeze_support

from mmo_app.ui.main_window import run


if __name__ == "__main__":
    freeze_support()
    run()
