import math
from mathutils import Vector, Matrix
from ..bin import pyluxcore
from .. import utils
from ..nodes.output import get_active_output
from ..utils.errorlog import LuxCoreErrorLog
from .image import ImageExporter


def convert(exporter, scene, depsgraph, context=None, is_camera_moving=False):
    prefix = "scene.camera."
    definitions = {}

    if context:
        # Viewport render
        view_cam_type = context.region_data.view_perspective

        if view_cam_type == "ORTHO":
            _view_ortho(scene, context, definitions)
        elif view_cam_type == "PERSP":
            _view_persp(scene, context, definitions)
        elif view_cam_type == "CAMERA":
            _view_camera(scene, context, definitions)
            _clipping(scene, definitions)
        else:
            raise NotImplementedError("Unknown context.region_data.view_perspective")
    else:
        # Final render
        _final(scene, definitions)
        _clipping(scene, definitions)

    _clipping_plane(scene, definitions)
    _motion_blur(scene, definitions, context, is_camera_moving)

    cam_props = utils.create_props(prefix, definitions)
    cam_props.Set(_get_volume_props(exporter, scene, depsgraph))
    return cam_props


def _view_ortho(scene, context, definitions):
    cam_matrix = Matrix(context.region_data.view_matrix).inverted()
    lookat_orig, lookat_target, up_vector = _calc_lookat(cam_matrix, scene)

    definitions["type"] = "orthographic"
    #zoom = 1.0275 * world_scale * context.region_data.view_distance * 35 / context.space_data.lens
    zoom = 1.0275 * context.region_data.view_distance * 35 / context.space_data.lens

    # Move the camera origin away from the viewport center to avoid clipping
    origin = Vector(lookat_orig)
    target = Vector(lookat_target)
    origin += (origin - target) * 50
    definitions["lookat.orig"] = list(origin)
    definitions["lookat.target"] = lookat_target
    definitions["up"] = up_vector

    definitions["screenwindow"] = utils.calc_screenwindow(zoom, 0, 0, scene, context)


def _view_persp(scene, context, definitions):
    cam_matrix = Matrix(context.region_data.view_matrix).inverted()
    lookat_orig, lookat_target, up_vector = _calc_lookat(cam_matrix, scene)
    definitions["lookat.orig"] = lookat_orig
    definitions["lookat.target"] = lookat_target
    definitions["up"] = up_vector

    definitions["type"] = "perspective"    
    zoom = 2.25

    definitions["fieldofview"] = math.degrees(2 * math.atan(16 / context.space_data.lens))
    definitions["screenwindow"] = utils.calc_screenwindow(zoom, 0, 0, scene, context)


def _view_camera(scene, context, definitions):
    camera = scene.camera

    if camera.type != "CAMERA":
        raise Exception(
            f"{camera.type} Objects as cameras are not supported, use a CAMERA object"
        )

    lookat_orig, lookat_target, up_vector = _calc_lookat(camera.matrix_world, scene)

    definitions["lookat.orig"] = lookat_orig
    definitions["lookat.target"] = lookat_target
    definitions["up"] = up_vector

    # Magic zoom formula for camera viewport zoom from Cycles export code
    # %blender_root%\intern\cycles\blender\blender_camera.cpp, line 666ff

    #zoom = 4 / ((math.sqrt(2) + context.region_data.view_camera_zoom / 50) ** 2) / world_scale
    zoom = 4 / ((math.sqrt(2) + context.region_data.view_camera_zoom / 50) ** 2)

    if camera.data.type == "ORTHO":
        definitions["type"] = "orthographic"
        #zoom *= 0.5*world_scale * camera.data.ortho_scale
        zoom *= 0.5 * camera.data.ortho_scale
    elif camera.data.type == "PANO":
        definitions["type"] = "environment"
    elif camera.data.type == "PERSP":
        definitions["type"] = "perspective"
        definitions["fieldofview"] = math.degrees(camera.data.angle)
        _depth_of_field(scene, definitions, context)
    else:
        raise NotImplementedError("Unknown camera.data.type")

    # Screenwindow
    definitions["screenwindow"] = utils.calc_screenwindow(zoom, camera.data.shift_x, camera.data.shift_y, scene, context)


def _final(scene, definitions):
    camera = scene.camera

    if camera.type != "CAMERA":
        raise Exception(
            f"{camera.type} Objects as cameras are not supported, use a CAMERA object"
        )

    lookat_orig, lookat_target, up_vector = _calc_lookat(camera.matrix_world, scene)
    definitions["lookat.orig"] = lookat_orig
    definitions["lookat.target"] = lookat_target
    definitions["up"] = up_vector
    zoom = 1

    if camera.data.type == "ORTHO":
        cam_type = "orthographic"
        #zoom = 0.5 * world_scale * camera.data.ortho_scale
        zoom = 0.5 * camera.data.ortho_scale

    elif camera.data.type == "PANO":
        cam_type = "environment"
    else:
        cam_type = "perspective"
    definitions["type"] = cam_type

    # Field of view
    if cam_type == "perspective":
        definitions["fieldofview"] = math.degrees(camera.data.angle)
        _depth_of_field(scene, definitions)

    # screenwindow (for border rendering and camera shift)
    definitions["screenwindow"] = utils.calc_screenwindow(zoom, camera.data.shift_x, camera.data.shift_y, scene)


def _depth_of_field(scene, definitions, context=None):
    camera = scene.camera
    settings = camera.data.luxcore

    if not camera.data.dof.use_dof or utils.in_material_shading_mode(context):
        return

    definitions["lensradius"] = (camera.data.lens / 1000) / (2 * camera.data.dof.aperture_fstop)

    if settings.use_autofocus:
        definitions["autofocus.enable"] = True
    elif dof_obj := camera.data.dof.focus_object:
        # Use distance along camera Z direction
        cam_matrix = camera.matrix_world
        lookat_orig = cam_matrix.to_translation()
        lookat_target = cam_matrix @ Vector((0, 0, -1))

        lookat_dir = (lookat_target - lookat_orig).normalized()
        dof_dir = dof_obj.matrix_world.to_translation() - lookat_orig

        definitions["focaldistance"] = abs(lookat_dir.dot(dof_dir))
    else:
        definitions["focaldistance"] = camera.data.dof.focus_distance

    bokeh = settings.bokeh
    if bokeh.non_uniform:
        distribution = bokeh.distribution
        if distribution == "CUSTOM" and not bokeh.image:
            distribution = "UNIFORM"

        definitions["bokeh.distribution.type"] = distribution
        definitions["bokeh.blades"] = bokeh.blades
        definitions["bokeh.power"] = bokeh.power

        anisotropy = bokeh.anisotropy
        if anisotropy > 0:
            x = 1
            y = 1 - anisotropy
        else:
            x = anisotropy + 1
            y = 1

        definitions["bokeh.scale.x"] = x
        definitions["bokeh.scale.y"] = y

        if distribution == "CUSTOM":
            try:
                filepath = ImageExporter.export(bokeh.image,
                                                bokeh.image_user,
                                                scene)
                definitions["bokeh.distribution.image"] = filepath
            except OSError as error:
                LuxCoreErrorLog.add_warning(f"Camera: {error}")
                definitions["bokeh.distribution.type"] = "UNIFORM"


def _clipping(scene, definitions):
    camera = scene.camera
    if not utils.is_valid_camera(camera):
        # Viewport render should work without camera
        return

    if camera.data.luxcore.use_clipping:
        clip_start = camera.data.clip_start
        clip_end = camera.data.clip_end

        definitions["cliphither"] = clip_start
        definitions["clipyon"] = clip_end

        # Show a warning if the clip settings don't make sense
        warning = ""
        if clip_start > clip_end:
            warning = "Clip start greater than clip end"
        if clip_start == clip_end:
            warning = "Clip start and clip end are exactly equal"

        if warning:
            msg = f'Camera: {warning}'
            LuxCoreErrorLog.add_warning(msg, obj_name=camera.name)


def _clipping_plane(scene, definitions):
    if not utils.is_valid_camera(scene.camera):
        # Viewport render should work without camera
        return
    cam_settings = scene.camera.data.luxcore

    if cam_settings.use_clipping_plane and cam_settings.clipping_plane:
        plane = cam_settings.clipping_plane
        normal = plane.rotation_euler.to_matrix() @ Vector((0, 0, 1))

        definitions.update({
            "clippingplane.enable": cam_settings.use_clipping_plane,
            "clippingplane.center": list(plane.location),
            "clippingplane.normal": list(normal),
        })
    else:
        definitions["clippingplane.enable"] = False


def _motion_blur(scene, definitions, context, is_camera_moving):
    if not utils.is_valid_camera(scene.camera):
        # Viewport render should work without camera
        return

    moblur_settings = scene.camera.data.luxcore.motion_blur
    if not moblur_settings.enable:
        return

    definitions["shutteropen"] = -moblur_settings.shutter / 2
    definitions["shutterclose"] = moblur_settings.shutter / 2

    # Don't export camera blur in viewport render
    if moblur_settings.camera_blur and not context and is_camera_moving:
        # Make sure lookup is defined - this function should be the last to modify it
        assert "lookat.orig" in definitions
        assert "lookat.target" in definitions
        assert "up" in definitions
        # Reset lookat - it's handled by motion.x.transformation
        definitions["lookat.orig"] = [0, 0, 0]
        definitions["lookat.target"] = [0, 0, -1]
        definitions["up"] = [0, 1, 0]
        # Note: camera motion system is defined in export/motion_blur.py


def _calc_lookat(cam_matrix, scene):
    lookat_orig = list(cam_matrix.to_translation())
    lookat_target = list(cam_matrix @ Vector((0, 0, -1)))
    up_vector = list(cam_matrix.to_3x3() @ Vector((0, 1, 0)))
    return lookat_orig, lookat_target, up_vector


def _get_volume_props(exporter, scene, depsgraph):
    props = pyluxcore.Properties()

    if not utils.is_valid_camera(scene.camera):
        # Viewport render should work without camera
        return props

    cam_settings = scene.camera.data.luxcore
    if volume_node_tree := cam_settings.volume:
        luxcore_name = utils.get_luxcore_name(volume_node_tree)
        active_output = get_active_output(volume_node_tree)

        try:
            active_output.export(exporter, depsgraph, props, luxcore_name)
            props.Set(pyluxcore.Property("scene.camera.volume", luxcore_name))
        except Exception as error:
            msg = f'Camera: {error}'
            LuxCoreErrorLog.add_warning(msg, obj_name=scene.camera.name)

    props.Set(pyluxcore.Property("scene.camera.autovolume.enable", cam_settings.auto_volume))
    return props
