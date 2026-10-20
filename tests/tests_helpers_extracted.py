from readlater.extractor.extract import Extracted


def extracted(url, text="word " * 200):
    return Extracted(url, url, "T", None, None, None, text)
