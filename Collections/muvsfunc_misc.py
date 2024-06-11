"""
Miscellaneous functions:
    GPS
    gauss
    freq_merge
    band_merge
    detail_enhancement
    SSR
    Wiener2
    tv
    BernsteinFilter
    GPA
    XDoG
    sbr_detail
    fade
    fast_mandelbrot
"""

import functools
import math
import vapoursynth as vs
from vapoursynth import core
import muvsfunc as muf
import mvsfunc as mvf
import typing

_is_api4: bool = hasattr(vs, "__api_version__") and vs.__api_version__.api_major == 4

def _get_array(frame, plane, read=True):
    if not read and frame.readonly:
        raise ValueError("Frame is readonly")

    if _is_api4:
        return frame[plane]
    else:
        if read:
            return frame.get_read_array(plane)
        else:
            return frame.get_write_array(plane)

def GPS(clip, gamma=None):
    """Get Power Spectrum

    Args:
        gamma: It enables viewing small valued responses in the spectral display.

    """

    w = clip.width
    h = clip.height
    max_w_h = max(w, h)

    clip = core.std.AddBorders(clip, right=max_w_h - w, bottom=max_w_h - h)
    clip = core.vcfreq.F2Quiver(clip, test=1, frad=16, fspec=[1,2,0,1,7], gamma=gamma)
    clip = core.std.CropRel(clip, 0, max_w_h // 2).resize.Bicubic(w, h)
    return clip


def gauss(clip, sigma=None, algo=0):
    """Gaussian filter using tcanny
    Borrowed from https://github.com/IFeelBloated/Oyster

    Args:
        sigma: Standard deviation of gaussian.

        algo: (int) Algorithm. 0:auto, 1:tcanny.TCanny(mode=-1), 2:bilateral.Gaussian()

    """

    if (algo == 0 and sigma is not None and sigma >= 10) or algo == 2:
        return core.bilateral.Gaussian(clip, sigma=sigma)
    else: # algo == 1 or (algo == 0 and (sigma is None or sigma < 10))
        return core.tcanny.TCanny(clip, sigma=sigma, mode=-1)


def freq_merge(src, flt, fun=None, **fun_args):
    """Replace high freq component in "src" with high freq component in "flt"
    Borrowed from https://github.com/IFeelBloated/Oyster

    Args:
        src, flt: Input.

        fun: (function) A low-pass filter. Default is gaussian.
    """

    if fun is None or not callable(fun):
        fun = gauss

    low_src = fun(src, **fun_args)
    low_flt = fun(flt, **fun_args)
    return core.std.Expr([low_src, flt, low_flt], ['y z - x +'])


def band_merge(src, flt, fun=None, fun_args1=None, fun_args2=None, cascade=True):
    """Replace frequencies within a certain range in "src" with frequencies within a certain range in "flt"

    Args:
        src, flt: I nput.

        fun: (function) A low-pass filter. Default is gaussian.

        cascade: (bool) Whether to cascade functions. Default is True.

    """

    if fun is None or not callable(fun):
        fun = gauss

    if fun_args1 == None:
        fun_args1 = {}

    if fun_args2 == None:
        fun_args2 = {}

    low_src1 = fun(src, **fun_args1)
    low_src2 = fun(low_src1 if cascade else src, **fun_args2)
    low_flt1 = fun(flt, **fun_args1)
    low_flt2 = fun(low_flt1 if cascade else flt, **fun_args2)
    return core.std.Expr([low_flt1, low_flt2, src, low_src1, low_src2], ['x y - b + a - z +'])


def detail_enhancement(clip, guidance=None, iter=3, radius=4, regulation=0.0005, fast=False, **args):
    """Novel detail enhancement filter using guided filter and defilter

    Args:
        clip: Gray scale.
        guidance: Guidance clip.

    """

    return muf.DeFilter(clip, muf.GuidedFilter, guidance=guidance, radius=radius, regulation=regulation, fast=fast, iteration=iter, **args)


def SSR(clip, sigma=50, full=None, **args):
    """Single-scale Retinex

    Args:
        clip: Input. Only the first plane will be processed.

        sigma: (int) Standard deviation of gaussian blur. Default is 50.

        full: (bool) Whether input clip is of full range. Default is None.

    Ref:
        [1] Jobson, D. J., Rahman, Z. U., & Woodell, G. A. (1997). Properties and performance of a center/surround retinex. IEEE transactions on image processing, 6(3), 451-462.

    """

    bits = clip.format.bits_per_sample
    sampleType = clip.format.sample_type
    isGray = clip.format.color_family == vs.GRAY

    if not isGray:
        clip_src = clip
        clip = mvf.GetPlane(clip)

    lowFre = gauss(clip, sigma=sigma, **args)

    clip = mvf.Depth(clip, 32, fulls=full)
    lowFre = mvf.Depth(lowFre, 32, fulls=full) # core.bilateral.Gaussian() doesn't support float input.

    expr = 'x 1 + log y 1 + log -'
    clip = core.std.Expr([clip, lowFre], [expr])

    stats = core.std.PlaneStats(clip, plane=[0])

    # Dynamic range stretching
    def Stretch(n, f, clip, core):
        alpha = f.props['PlaneStatsMax'] - f.props['PlaneStatsMin']
        beta = f.props['PlaneStatsMin']

        expr = 'x {beta} - {alpha} /'.format(beta=beta, alpha=alpha)
        return core.std.Expr([clip], [expr])

    clip = core.std.FrameEval(clip, functools.partial(Stretch, clip=clip, core=core), prop_src=stats)

    clip = mvf.Depth(clip, depth=bits, sample=sampleType, fulld=full)

    if not isGray:
        clip = core.std.ShufflePlanes([clip, clip_src], list(range(clip_src.format.num_planes)), clip_src.format.color_family)

    return clip


def Wiener2(input, radius_v=3, radius_h=None, noise=None, **depth_args):
    """2-D adaptive noise-removal filtering. (wiener2 from MATLAB)

    Wiener2 lowpass filters an intensity image that has been degraded by constant power additive noise.
    Wiener2 uses a pixel-wise adaptive Wiener method based on statistics estimated from a local neighborhood of each pixel.

    Estimate of the additive noise power will not be returned.

    Args:
        input: Input clip. Only the first plane will be processed.

        radius_v, radius_h: (int) Size of neighborhoods to estimate the local image mean and standard deviation. The size is (radius_v*2-1) * (radius_h*2-1).
            If "radius_h" is None, it will be set to "radius_v".
            Default is 3.

        noise: (float) Variance of addictive noise. If it is not given, average of all the local estimated variances will be used.
            Default is {}.

        depth_args: (dict) Additional arguments passed to mvf.Depth() in the form of keyword arguments.
            Default is {}.

    Ref:
        [1] Lim, J. S. (1990). Two-dimensional signal and image processing. Englewood Cliffs, NJ, Prentice Hall, 1990, 710 p, p. 538, equations 9.26, 9.27, and 9.29.
        [2] 2-D adaptive noise-removal filtering - MATLAB wiener2 - MathWorks (https://www.mathworks.com/help/images/ref/wiener2.html)

    """

    funcName = 'Wiener2'

    if not isinstance(input, vs.VideoNode) or input.format.num_planes > 1:
        raise TypeError(funcName + ': \"input\" must be a gray-scale/single channel clip!')

    bits = input.format.bits_per_sample
    sampleType = input.format.sample_type

    if radius_h is None:
        radius_h = radius_v

    input32 = mvf.Depth(input, depth=32, sample=vs.FLOAT, **depth_args)

    localMean = muf.BoxFilter(input32, radius_h+1, radius_v+1)
    localVar = muf.BoxFilter(core.std.Expr([input32], ['x dup *']), radius_h+1, radius_v+1)
    localVar = core.std.Expr([localVar, localMean], ['x y dup * -'])

    if noise is None:
        localVarStats = core.std.PlaneStats(localVar, plane=[0])

        def FLT(n, f, clip, core, localMean, localVar):
            noise = f.props['PlaneStatsAverage']

            return core.std.Expr([clip, localMean, localVar], ['y z {noise} - 0 max z {noise} max / x y - * +'.format(noise=noise)])

        flt = core.std.FrameEval(input32, functools.partial(FLT, clip=input32, core=core, localMean=localMean, localVar=localVar), prop_src=[localVarStats])
    else:
        flt = core.std.Expr([input32, localMean, localVar], ['y z {noise} - 0 max z {noise} max / x y - * +'.format(noise=noise)])

    return mvf.Depth(flt, depth=bits, sample=sampleType, **depth_args)


def tv(I, iter=5, dt=None, ep=1, lam=0, I0=None, C=0):
    """Total Variation Denoising

    Args:
        I: Input. Recommended to input floating type clip.

        iter: (int) Num of iterations. Default is 5.

        dt: (float) Time step. Default is ep/5.

        ep: (float) Epsilon (of gradient regularization). Default is 1.

        lam: (float) Fidelity term lambda. Default is 0.

        I0: (clip) Input (noisy) image. Default is "I".


    Ref:
        [1] Rudin, L. I., Osher, S., & Fatemi, E. (1992). Nonlinear total variation based noise removal algorithms. Physica D: Nonlinear Phenomena, 60(1-4), 259-268.
        [2] Total Variation Denoising : http://visl.technion.ac.il/~gilboa/PDE-filt/tv_denoising.html

    """

    if dt is None:
        dt = ep / 5

    if I0 is None:
        I0 = I

    ep2 = ep * ep

    isFloat = I.format.sample_type == vs.FLOAT
    neutral = 0 if isFloat else muf.scale(128, I.format.bits_per_sample)

    for i in range(iter):
        I_x = core.std.Convolution(I, [-1, 0, 1], divisor=2, bias=neutral, mode='h') # correct
        I_y = core.std.Convolution(I, [-1, 0, 1], divisor=2, bias=neutral, mode='v') # correct
        I_xx = core.std.Convolution(I, [1, -2, 1], divisor=1 if isFloat else 4, bias=neutral, mode='h') # x4
        I_yy = core.std.Convolution(I, [1, -2, 1], divisor=1 if isFloat else 4, bias=neutral, mode='v') # x4
        Dp = core.std.Convolution(I, [1, 0, 0, 0, 0, 0, 0, 0, 1], divisor=2)
        Dm = core.std.Convolution(I, [0, 0, 1, 0, 0, 0, 1, 0, 0], divisor=2)
        I_xy = core.std.Expr([Dp, Dm], ['x y - 2 / {} +'.format(neutral)]) # correct

        if isFloat:
            expr = 'x {dt} a {ep2} z dup * + * 2 y * z * b * - c {ep2} y dup * + * + {ep2} y dup * + z dup * + 1.5 pow / {lam} d x - {C} + * + * +'.format(dt=dt, ep2=ep2, lam=lam, C=C)
        else: # isInteger
            expr = 'x {dt} a {neutral} - 4 * {ep2} z {neutral} - dup * + * 2 y {neutral} - * z {neutral} - * b {neutral} - * - c {neutral} - 4 * {ep2} y {neutral} - dup * + * + {ep2} y {neutral} - dup * + z {neutral} - dup * + 1.5 pow / {lam} d x - {C} + * + * +'.format(dt=dt, neutral=neutral, ep2=ep2, lam=lam, C=C)

        I = core.std.Expr([I, I_x, I_y, I_xx, I_xy, I_yy, I0], [expr])

    return I


def BernsteinFilter(clip, iter=30, **depth_args):
    """Bernstein Filter

    Bernstein filter is an efficient filter solver, which can implicitly minimize the mean curvature.

    Internal precision is always float.

    Args:
        clip: Input.

        iter: (int) Num of iterations. Default is 30

        depth_args: (dict) Additional arguments passed to mvf.Depth() in the form of keyword arguments.
            Default is {}.

    Ref:
        [1] Gong, Y. (2016, March). Bernstein filter: A new solver for mean curvature regularized models. In Acoustics, Speech and Signal Processing (ICASSP), 2016 IEEE International Conference on (pp. 1701-1705). IEEE.

    """

    bits = clip.format.bits_per_sample
    sample = clip.format.sample_type

    clip = mvf.Depth(clip, depth=32, sample=vs.FLOAT, **depth_args)

    for i in range(iter):
        d1 = core.std.Convolution(clip, [1, -2, 1], divisor=2, mode='h')
        d2 = core.std.Convolution(clip, [1, -2, 1], divisor=2, mode='v')
        clip = core.std.Expr([clip, d1, d2], ['y abs z abs < x y + x z + ?'])

    return mvf.Depth(clip, depth=bits, sample=sample, **depth_args)


def GPA(clip, sigmaS=3, sigmaR=0.15, mode=0, iteration=0, eps=1e-3, **depth_args):
    """Fast and Accurate Bilateral Filtering using Gaussian-Polynomial Approximation

    This filter approximates the bilateral filter when the range kernel is Gaussian.
    The exponential function of the weight function of bilateral filter is approximated,
    and the bilateral is therefore decomposed into a series of spatial convolutions.

    The number of iteration depends on the value of "sigmaR", which increases as "sigmaR" decreases.
    A small value of "sigmaR" may lead to presicion problem.

    All the internal calculations are done at 32-bit float.

    Part of description of bilateral filter is copied from
    https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Bilateral

    Args:
        clip: Input clip.

        sigmaS: (float) Sigma of Gaussian function to calculate spatial weight.
            The scale of this parameter is equivalent to pixel distance.
            Larger sigmaS results in larger filtering radius as well as stronger smoothing.
            Default is 3.

        sigmaR: (float) Sigma of Gaussian function to calculate range weight.
            The scale of this parameter is the same as pixel value ranging in [0,1].
            Smaller sigmaR preserves edges better, may also leads to weaker smoothing.
            It should be pointed out that a small "sigmaR" results in more iteration and higher error.
            Default is 0.15.

        mode: (0 or 1) 0: Guassian bilateral filter, 1: Box bilateral filter
            Default is 0.

        iteration: (int) Number of iteration or the order of approximation.
            If it is 0, it is calculated automatically according to "sigmaR" and "eps".
            Default is 0.

        eps: (float) Filtering Accuracy.
            Default is 1e-3.

        depth_args: (dict) Additional arguments passed to mvf.Depth().
            Default is {}.

    Ref:
        [1] Chaudhury, K. N., & Dabhade, S. D. (2016). Fast and provably accurate bilateral filtering. IEEE Transactions on Image Processing, 25(6), 2519-2528.
        [2] http://www.mathworks.com/matlabcentral/fileexchange/56158

    """

    def estimate_iteration(sigmaR, T, eps):
        if sigmaR > 70:
            return 10
        elif sigmaR < 5:
            return 800
        else:
            lam = (T / sigmaR) ** 2
            p = 1 + math.log(lam)
            q = -lam - math.log(eps)
            t = q / math.e / lam
            W = t - t ** 2 + 1.5 * t ** 3 - (8 / 3) * t ** 4
            N = min(max(q / W, 10), 300)

            if sigmaR < 30:
                for i in range(5):
                    N -= (N * math.log(N) - p * N - q) / (math.log(N) + 1 - p)

            return math.ceil(N)

    T = 0.5
    bits = clip.format.bits_per_sample
    sampleType = clip.format.sample_type

    if mode == 0: # Gaussian bilateral filter
        Filter = functools.partial(core.tcanny.TCanny, sigma=sigmaS, mode=-1)
    else: # Box bilateral filter
        Filter = functools.partial(muf.BoxFilter, radius=sigmaS + 1)

    if iteration == 0:
        iteration = estimate_iteration(sigmaR * 255, T, eps)

    clip = mvf.Depth(clip, depth=32, sample=vs.FLOAT, **depth_args)

    H = core.std.Expr(clip, f'x {T} - {sigmaR} /')
    F = core.std.Expr(H, '-0.5 x dup * * exp')
    G = core.std.BlankClip(clip, color=[1] * clip.format.num_planes)
    P = core.std.BlankClip(clip, color=[0] * clip.format.num_planes)
    Q = core.std.BlankClip(clip, color=[0] * clip.format.num_planes)
    Fbar = Filter(F)

    for i in range(1, iteration+1):
        sqrt_i = math.sqrt(i)
        inv_sqrt_i = 1 / sqrt_i
        Q = core.std.Expr([Q, G, Fbar], 'x y z * +')
        F = core.std.Expr([H, F], f'x y * {inv_sqrt_i} *')
        Fbar = Filter(F)
        P = core.std.Expr([P, G, Fbar], f'x y z * {sqrt_i} * +')
        G = core.std.Expr([H, G], f'x y * {inv_sqrt_i} *')

    res = core.std.Expr([P, Q], f'x {sigmaR} * y 1e-5 + / {T} +')

    return mvf.Depth(res, depth=bits, sample=sampleType, **depth_args)


def XDoG(clip, sigma=1.0, k=1.6, p=20, epsilon=0.7, lamda=0.01):
    """XDoG - An eXtended difference-of-Gaussian filter

    Args:
        clip: Input clip.

        sigma: (float) Strength of gaussian filter.
            Default is 1.

        k: (float) Amplifier of "sigma" for second gaussian filtering.
            Default is 1.6.

        p: (float) Amplifier of difference of gaussian.
            Default is 20.

        epsilon: (float, 0~1) Threshold of DoG response. Scaled automatically.
            Default is 0.7.

        lamda: (float) Multiplier in the thresholding function.
            Default is 0.01.

    Ref:
        [1] Winnemöller, H., Kyprianidis, J. E., & Olsen, S. C. (2012). XDoG: an extended difference-of-Gaussians compendium including advanced image stylization. Computers & Graphics, 36(6), 740-753.

    """

    bits = clip.format.bits_per_sample
    peak =  (1 << bits) - 1
    epsilon = muf.scale(epsilon, bits)

    f1 = core.tcanny.TCanny(clip, sigma=sigma, mode=-1)
    f2 = core.tcanny.TCanny(clip, sigma=sigma * k, mode=-1)

    return core.std.Expr([f1, f2], f'x y - {p} * x + {epsilon} >= 1 2 2 2 x y - {p} * x + {epsilon} - {lamda} * * exp 1 + / - ? {peak} *')


def sbr_detail(clip, r=1, planes=None, mode=1):
    """sbr() inspired detail detection algorithm

    Code is modified from sbr() in https://github.com/HomeOfVapourSynthEvolution/havsfunc/blob/master/havsfunc.py.

    args:
        clip: RGB/YUV/Gray, 8..16 bit integer, 16..32 bit float.

        r: (int) Radius in pixels of the smoothing filter.
            Default is 1.

        planes: (int []) Whether to process the corresponding plane.
            By default, every plane will be processed.
            The unprocessed planes will be copied from "input".

        mode: (int, 0~2) Detail detection method, insensitive to sensitive.
            The result of mode 2 is a combination os mode 1 and mode 2.
            Default is 1.
    """

    funcName = 'sbr_detail'

    if not isinstance(clip, vs.VideoNode):
        raise TypeError(funcName + ': This is not a clip')

    if planes is None:
        planes = list(range(clip.format.num_planes))
    elif isinstance(planes, int):
        planes = [planes]

    if clip.format.sample_type == vs.INTEGER:
        neutral = 1 << (clip.format.bits_per_sample - 1)
        peak = (1 << clip.format.bits_per_sample) - 1
    else: # clip.format.sample_type == vs.FLOAT
        neutral = 0.5
        peak = 1.0

    matrix1 = [1, 2, 1, 2, 4, 2, 1, 2, 1] # RemoveGrain(11)
    matrix2 = [1, 1, 1, 1, 1, 1, 1, 1, 1] # RemoveGrain(20)

    RG11 = core.std.Convolution(clip, matrix=matrix1, planes=planes)
    for i in range(r - 1):
        RG11 = core.std.Convolution(RG11, matrix=matrix2, planes=planes)

    RG11D = core.std.MakeDiff(clip, RG11, planes=planes)

    RG11DS = core.std.Convolution(RG11D, matrix=matrix1, planes=planes)
    for i in range(r - 1):
        RG11DS = core.std.Convolution(RG11DS, matrix=matrix2, planes=planes)

    if mode == 0:
        expr = f'x y - x {neutral} - * 0 < {peak} 0 ?'
    elif mode == 1:
        expr = f'x y - abs x {neutral} - abs < {peak} 0 ?'
    elif mode == 2:
        expr = f'x y - x {neutral} - * 0 < x y - abs x {neutral} - abs < or {peak} 0 ?'

    detail_mask = core.std.Expr([RG11D, RG11DS], [expr if i in planes else '' for i in range(clip.format.num_planes)])

    return detail_mask


def fade(clip, start=0, end=None, mode='in', base=None):
    """Fade-in/out effect implementation

    args:
        clip: RGB/YUV/Gray, 8..16 bit integer, 16..32 bit float.

        start: (int) Frame number of started frame.
            Default is 0.

        end: (int) Frame number of ended frame.
            Default points to the last frame.

        mode: ("in" or "out") Fade mode.
            Default is "in".

        base: (clip) Base clip of fade effect.
            Default is black picture.
    """

    funcName = 'fade'

    if not isinstance(clip, vs.VideoNode):
        raise TypeError(funcName + ': This is not a clip')

    if end is None:
        end = clip.num_frames - 1

    def fade_core(n, clip, start=None, end=None, mode=None, base=None):
        if n < start or n > end or end - start <= 0:
            return clip
        else:
            length = end - start

            if mode == 'in':
                i = (n - start) / length
            elif mode == 'out':
                i = (end - n) / length
            else:
                raise ValueError('Unknown fading mode.')

            if base is None:
                y_expr = 'x {} *'.format(i)

                if clip.format.color_family != vs.YUV or clip.format.sample_type == vs.FLOAT:
                    return core.std.Expr([clip], [y_expr])
                else:
                    neutral = 1 << (clip.format.bits_per_sample - 1)
                    uv_expr = 'x {} * {} +'.format(i, (1 - i) * neutral)
                    return core.std.Expr([clip], [y_expr, uv_expr])
            else:
                return core.std.Expr([clip, base], ['x {} * y {} * +'.format(i, 1 - i)])

    return core.std.FrameEval(clip, functools.partial(fade_core, clip=clip, start=start, end=end, mode=mode, base=base))


def fast_mandelbrot(width=1920, height=1280, iterations=50,
    real_range=(-2, 1), imag_range=(-1, 1), c=0+0j, julia_set=False, backend=None):

    import array

    def meshgrid_core(n, f, low, high, horizontal):
        assert low < high, f"{low} < {high}"

        f = f.copy()
        mem_view = _get_array(f, plane=0, read=False)
        height, width = mem_view.shape

        if horizontal:
            data = array.array('f', (((high - low) * j / (width - 1) + low) for j in range(width)))

            for i in range(height):
                if _is_api4:
                    for j in range(width):
                        mem_view[i, j] = data[j]
                else:
                    mem_view[i, :] = data
        else:
            for i in range(height):
                if _is_api4:
                    for j in range(width):
                        mem_view[i, j] = (low - high) * i / (height - 1) + high
                else:
                    mem_view[i, :] = array.array('f', [(low - high) * i / (height - 1) + high]) * width

        return f

    c = complex(c)

    ones = core.std.BlankClip(format=vs.GRAYS, width=width, height=height, length=1, color=1)

    if hasattr(core, "akarin"):
        features = core.akarin.Version()["expr_features"]

        if b"X" in features and b"width" in features:
            z_real = core.akarin.Expr([ones], f"{real_range[1] - real_range[0]} X * width 1 - / {real_range[0]} +")
        else:
            z_real = core.std.ModifyFrame(
                ones, ones,
                functools.partial(meshgrid_core, horizontal=True, low=real_range[0], high=real_range[1]))

        if b"Y" in features and b"height" in features:
            z_imag = core.akarin.Expr([ones], f"{imag_range[0] - imag_range[1]} Y * height 1 - / {imag_range[1]} +")
        else:
            z_imag = core.std.ModifyFrame(
                ones, ones,
                functools.partial(meshgrid_core, horizontal=False, low=imag_range[0], high=imag_range[1]))

    if julia_set:
        inner = (
            f"dup2 dup2 * dup0 + {c.imag} + " # new z_imag
            f"dup3 dup0 * dup3 dup0 * - {c.real} + " # new z_real
            "dup1 dup0 * dup1 dup0 * + 4 < " # mask
            "swap1 dup1 swap6 ? " # update z_real
            "swap4 swap1 dup1 swap4 ? " # update z_imag
            f"swap2 swap1 dup0 {1/iterations} - swap1 ? " # update counter
        )

        expr = f"x y z {inner * iterations} 1 swap2 ? 1 swap2 ?"

    else:
        inner = (
            "dup2 dup2 * dup0 + y + " # new z_imag
            "dup3 dup0 * dup3 dup0 * - x + " # new z_real
            "dup1 dup0 * dup1 dup0 * + 4 < " # mask
            "swap1 dup1 swap6 ? " # update z_real
            "swap4 swap1 dup1 swap4 ? " # update z_imag
            f"swap2 swap1 dup0 {1/iterations} - swap1 ? " # update counter
        )

        expr = f"{c.real} {c.imag} z {inner * iterations} 1 swap2 ? 1 swap2 ?"

    if backend is None:
        if hasattr(core, "akarin"):
            return core.akarin.Expr([z_real, z_imag, ones], expr)
        else:
            return core.std.Expr([z_real, z_imag, ones], expr)
    else:
        return backend([z_real, z_imag, ones], expr)

