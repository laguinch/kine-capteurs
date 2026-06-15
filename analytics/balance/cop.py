def cop_from_corners(av_d, av_g, ar_g, ar_d):
    total = av_d + av_g + ar_g + ar_d
    if total == 0:
        return 0.0, 0.0
    x = ((av_d + ar_d) - (av_g + ar_g)) / total
    y = ((av_d + av_g) - (ar_d + ar_g)) / total
    return x, y
