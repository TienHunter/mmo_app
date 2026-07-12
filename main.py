from multiprocessing import freeze_support

from mmo_app.desktop import run


if __name__ == "__main__":
    freeze_support()
    run()
