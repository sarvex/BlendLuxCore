import bpy
from time import time
from .. import utils
import array


def convert(smoke_obj, channel, depsgraph):
    start = time()

    smoke_domain_mod = utils.find_smoke_domain_modifier(smoke_obj)

    if smoke_domain_mod is None:
        msg = f'Object "{smoke_obj.name}" is not a smoke domain'
        raise Exception(msg)

    settings = smoke_domain_mod.domain_settings

    if channel == "density":
        grid = settings.density_grid
    elif channel == "flame":
        grid = settings.flame_grid
    elif channel == "heat":
        grid = settings.heat_grid
    elif channel == "temperature":
        grid = settings.temperature_grid
    elif channel == "color":
        grid = settings.color_grid
    elif channel == "velocity":
        grid = settings.velocity_grid
    else:
        raise NotImplementedError(f"Unknown channel type {channel}")

    # Prevent a crash
    if len(grid) == 0:
        msg = f'Object "{smoke_obj.name}": No smoke data (simulate some frames first)'
        raise Exception(msg)

    # We have to convert Blender's bpy_prop_array because it doesn't support the Python buffer interface.
    # We use an array instead of a list here to save a lot of memory (list would use doubles instead of floats).
    channeldata = array.array("f", grid)

    # The smoke resolution along the x, y, z axis
    resolution = list(settings.domain_resolution)

    # Note: Velocity and heat data is always low-resolution. (Comment from Cycles source code)
    if bpy.app.version[:2] < (2, 82):
        if settings.use_high_resolution and channel not in {"velocity", "heat"}:
            resolution = [res * (settings.amplify + 1) for res in resolution]
    elif settings.use_noise:
        resolution = [res * settings.noise_scale for res in resolution]

    print("conversion to array took %.3f s" % (time() - start))

    return resolution, channeldata
