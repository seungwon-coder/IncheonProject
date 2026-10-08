"""Aspect-ratio-preserving display scaling, independent of camera capture."""
def fitted_size(source, bounds):
    w,h=source
    bw,bh=bounds
    scale=min(max(1,bw)/w,max(1,bh)/h)
    return max(1,int(w*scale)),max(1,int(h*scale))
