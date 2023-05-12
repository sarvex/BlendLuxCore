from collections import OrderedDict
from ..bin import pyluxcore
from .. import utils
from .image import ImageExporter
from ..utils.errorlog import LuxCoreErrorLog


def convert(scene, context=None, index=0):
    try:
        prefix = "film.imagepipelines.%03d." % index
        definitions = OrderedDict()

        if utils.in_material_shading_mode(context):
            index = _output_switcher(definitions, 0, "ALBEDO")
            _exposure_compensated_tonemapper(definitions, index, scene)
            return utils.create_props(prefix, definitions)

        if utils.using_photongi_debug_mode(context, scene):
            _exposure_compensated_tonemapper(definitions, 0, scene)
            return utils.create_props(prefix, definitions)

        if not utils.is_valid_camera(scene.camera):
            # Can not work without a camera
            _fallback(definitions)
            return utils.create_props(prefix, definitions)

        convert_defs(context, scene, definitions, 0)

        return utils.create_props(prefix, definitions)
    except Exception as error:
        import traceback
        traceback.print_exc()
        LuxCoreErrorLog.add_warning(f'Imagepipeline: {error}')
        return pyluxcore.Properties()


def convert_defs(context, scene, definitions, plugin_index, define_radiancescales=True):
    pipeline = scene.camera.data.luxcore.imagepipeline
    using_filesaver = utils.using_filesaver(context, scene)
    # Start index of plugins. Some AOVs prepend their own plugins.
    index = plugin_index

    # Make sure the imagepipeline does nothing when no plugins are enabled
    definitions[f"{str(index)}.type"] = "NOP"
    index += 1

    if pipeline.tonemapper.enabled:
        index = convert_tonemapper(definitions, index, pipeline.tonemapper)

    if context and scene.luxcore.viewport.get_denoiser(context) == "OPTIX":
        definitions[f"{str(index)}.type"] = "OPTIX_DENOISER"
        definitions[f"{str(index)}.sharpness"] = 0
        definitions[f"{str(index)}.minspp"] = scene.luxcore.viewport.min_samples
        index += 1

    if use_backgroundimage(context, scene):
        # Note: Blender expects the alpha to be NOT premultiplied, so we only
        # premultiply it when the backgroundimage plugin is used
        index = _premul_alpha(definitions, index)
        index = _backgroundimage(definitions, index, pipeline.backgroundimage, scene)

    if pipeline.mist.is_enabled(context):
        index = _mist(definitions, index, pipeline.mist)

    if pipeline.bloom.is_enabled(context):
        index = _bloom(definitions, index, pipeline.bloom)

    if pipeline.coloraberration.is_enabled(context):
        index = _coloraberration(definitions, index, pipeline.coloraberration)

    if pipeline.vignetting.is_enabled(context):
        index = _vignetting(definitions, index, pipeline.vignetting)

    if pipeline.white_balance.is_enabled(context):
        index = _white_balance(definitions, index, pipeline.white_balance)

    if pipeline.camera_response_func.is_enabled(context):
        index = _camera_response_func(definitions, index, pipeline.camera_response_func, scene)

    gamma_corrected = False
    if pipeline.color_LUT.is_enabled(context):
        index, gamma_corrected = _color_LUT(definitions, index, pipeline.color_LUT, scene)

    if pipeline.contour_lines.is_enabled(context):
        index = _contour_lines(definitions, index, pipeline.contour_lines)

    if using_filesaver and not gamma_corrected:
        # Needs gamma correction (Blender applies it for us,
        # but now we export for luxcoreui)
        index = _gamma(definitions, index)

    if define_radiancescales:
        _lightgroups(definitions, scene)

    return index


def use_backgroundimage(context, scene):
    viewport_in_camera_view = context and context.region_data.view_perspective == "CAMERA"
    final_render = not context
    pipeline = scene.camera.data.luxcore.imagepipeline
    return pipeline.backgroundimage.is_enabled(context) and (final_render or viewport_in_camera_view)


def _fallback(definitions):
    """
    Fallback imagepipeline if no camera is in the scene
    """
    index = 0
    definitions[f"{index}.type"] = "TONEMAP_LINEAR"
    definitions[f"{index}.scale"] = 1


def _exposure_compensated_tonemapper(definitions, index, scene):
    definitions[f"{str(index)}.type"] = "TONEMAP_LINEAR"
    definitions[f"{str(index)}.scale"] = 1 / pow(2, (scene.view_settings.exposure))
    return index + 1


def convert_tonemapper(definitions, index, tonemapper):
    # If "Auto Brightness" is enabled, put an autolinear tonemapper
    # in front of the linear tonemapper
    if tonemapper.type == "TONEMAP_LINEAR" and tonemapper.use_autolinear:
        definitions[f"{str(index)}.type"] = "TONEMAP_AUTOLINEAR"
        index += 1

    # Main tonemapper
    definitions[f"{str(index)}.type"] = tonemapper.type

    if tonemapper.type == "TONEMAP_LINEAR":
        definitions[f"{str(index)}.scale"] = tonemapper.linear_scale
    elif tonemapper.type == "TONEMAP_REINHARD02":
        definitions[f"{str(index)}.prescale"] = tonemapper.reinhard_prescale
        definitions[f"{str(index)}.postscale"] = tonemapper.reinhard_postscale
        definitions[f"{str(index)}.burn"] = tonemapper.reinhard_burn
    elif tonemapper.type == "TONEMAP_LUXLINEAR":
        definitions[f"{str(index)}.fstop"] = tonemapper.fstop
        definitions[f"{str(index)}.exposure"] = tonemapper.exposure
        definitions[f"{str(index)}.sensitivity"] = tonemapper.sensitivity

    return index + 1


def _premul_alpha(definitions, index):
    definitions[f"{str(index)}.type"] = "PREMULTIPLY_ALPHA"
    return index + 1


def _backgroundimage(definitions, index, backgroundimage, scene):
    if backgroundimage.image is None:
        return index

    try:
        filepath = ImageExporter.export(backgroundimage.image,
                                        backgroundimage.image_user,
                                        scene)
    except OSError as error:
        LuxCoreErrorLog.add_warning(f"Imagepipeline: {error}")
        # Skip this plugin
        return index

    definitions[f"{str(index)}.type"] = "BACKGROUND_IMG"
    definitions[f"{str(index)}.file"] = filepath
    definitions[f"{str(index)}.gamma"] = backgroundimage.gamma
    definitions[f"{str(index)}.storage"] = backgroundimage.storage
    return index + 1


def _mist(definitions, index, mist):
    definitions[f"{str(index)}.type"] = "MIST"
    definitions[f"{str(index)}.color"] = list(mist.color)
    definitions[f"{str(index)}.amount"] = mist.amount / 100
    definitions[f"{str(index)}.startdistance"] = mist.start_distance
    definitions[f"{str(index)}.enddistance"] = mist.end_distance
    definitions[f"{str(index)}.excludebackground"] = mist.exclude_background
    return index + 1


def _bloom(definitions, index, bloom):
    definitions[f"{str(index)}.type"] = "BLOOM"
    definitions[f"{str(index)}.radius"] = bloom.radius / 100
    definitions[f"{str(index)}.weight"] = bloom.weight / 100
    return index + 1


def _coloraberration(definitions, index, coloraberration):
    definitions[f"{str(index)}.type"] = "COLOR_ABERRATION"
    amount_x = coloraberration.amount / 100
    amount_y = coloraberration.amount_y / 100
    if coloraberration.uniform:
        definitions[f"{str(index)}.amount"] = amount_x
    else:
        definitions[f"{str(index)}.amount"] = [amount_x, amount_y]
    return index + 1


def _vignetting(definitions, index, vignetting):
    definitions[f"{str(index)}.type"] = "VIGNETTING"
    definitions[f"{str(index)}.scale"] = vignetting.scale / 100
    return index + 1


def _white_balance(definitions, index, white_balance):
    definitions[f"{str(index)}.type"] = "WHITE_BALANCE"
    definitions[f"{str(index)}.temperature"] = white_balance.temperature
    definitions[f"{str(index)}.reverse"] = white_balance.reverse
    definitions[f"{str(index)}.normalize"] = True
    return index + 1


def _camera_response_func(definitions, index, camera_response_func, scene):
    if camera_response_func.type == "PRESET":
        name = camera_response_func.preset
    elif camera_response_func.type == "FILE":
        try:
            library = scene.camera.data.library
            name = utils.get_abspath(camera_response_func.file, library,
                                     must_exist=True, must_be_existing_file=True)
        except OSError as error:
            # Make the error message more precise
            LuxCoreErrorLog.add_warning(
                f'Could not find .crf file at path "{camera_response_func.file}" ({error})'
            )
            name = None
    else:
        raise NotImplementedError(f"Unknown crf type: {camera_response_func.type}")

    # Note: preset or file are empty strings until the user selects something
    if name:
        definitions[f"{str(index)}.type"] = "CAMERA_RESPONSE_FUNC"
        definitions[f"{str(index)}.name"] = name
        return index + 1
    else:
        return index


def _color_LUT(definitions, index, color_LUT, scene):
    try:
        library = scene.camera.data.library
        filepath = utils.get_abspath(color_LUT.file, library,
                                     must_exist=True, must_be_existing_file=True)
    except OSError as error:
        # Make the error message more precise
        LuxCoreErrorLog.add_warning(
            f'Could not find .cube file at path "{color_LUT.file}" ({error})'
        )
        filepath = None

    if filepath:
        gamma_corrected = color_LUT.input_colorspace == "SRGB_GAMMA_CORRECTED"
        if gamma_corrected:
            index = _gamma(definitions, index)

        definitions[f"{str(index)}.type"] = "COLOR_LUT"
        definitions[f"{str(index)}.file"] = filepath
        definitions[f"{str(index)}.strength"] = color_LUT.strength / 100
        return index + 1, gamma_corrected
    else:
        return index, False


def _contour_lines(definitions, index, contour_lines):
    definitions[f"{str(index)}.type"] = "CONTOUR_LINES"
    definitions[f"{str(index)}.range"] = contour_lines.contour_range
    definitions[f"{str(index)}.scale"] = contour_lines.scale
    definitions[f"{str(index)}.steps"] = contour_lines.steps
    definitions[f"{str(index)}.zerogridsize"] = contour_lines.zero_grid_size
    return index + 1


def _gamma(definitions, index):
    definitions[f"{str(index)}.type"] = "GAMMA_CORRECTION"
    definitions[f"{str(index)}.value"] = 2.2
    return index + 1


def _output_switcher(definitions, index, channel):
    definitions[f"{str(index)}.type"] = "OUTPUT_SWITCHER"
    definitions[f"{str(index)}.channel"] = channel
    return index + 1


def _lightgroups(definitions, scene):
    lightgroups = scene.luxcore.lightgroups

    _lightgroup(definitions, lightgroups.default, 0)

    for i, group in enumerate(lightgroups.custom):
        # +1 to group_id because default group is id 0, but not in the list
        group_id = i + 1
        _lightgroup(definitions, group, group_id)


def _lightgroup(definitions, group, group_id):
    prefix = f"radiancescales.{str(group_id)}."
    definitions[f"{prefix}enabled"] = group.enabled
    definitions[f"{prefix}globalscale"] = group.gain

    if group.use_rgb_gain:
        definitions[f"{prefix}rgbscale"] = list(group.rgb_gain)

    if group.use_temperature:
        definitions[f"{prefix}temperature"] = group.temperature
