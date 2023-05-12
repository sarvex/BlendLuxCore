#!/usr/bin/env python3
#
# Note: On Windows, you need to have the git binary in your PATH for this script to work.

import argparse
import os
import urllib.request
import urllib.error
import subprocess
import shutil
import tarfile
import zipfile
import stat
import uuid
import platform

# From https://docs.python.org/3/library/shutil.html#rmtree-example
def remove_readonly(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)

def rmtree(path):
    if platform.system() == "Windows":
        # We have to rename the directory/file before removing it, otherwise it
        # leaves a locked "shadow" behind and blocks any attempt to create a
        # new directory/file with the same name

        head, tail = os.path.split(path)
        if not tail:
            # It is a directory, go up one level
            head = os.path.dirname(head)

        temp_name = str(uuid.uuid4())
        temp_path = os.path.join(head, temp_name)
        os.rename(path, temp_path)
        shutil.rmtree(temp_path, ignore_errors=False, onerror=remove_readonly)
    else:
        shutil.rmtree(path)


script_dir = os.path.dirname(os.path.realpath(__file__))

# These are the same as in BlendLuxCore/bin/get_binaries.py
# (apart from missing luxcoreui, that's only for developers)
LINUX_FILES = [
    "libembree3.so.3", "libtbb.so.12", "libtbb.so.2", "libtbbmalloc.so.2",
    "pyluxcore.so", "pyluxcoretools.zip",
    "libOpenImageDenoise.so.1",
    "libnvrtc-builtins.so",
    "libnvrtc-builtins.so.11.0",
    "libnvrtc-builtins.so.11.0.194",
    "libnvrtc.so",
    "libnvrtc.so.11.0",
    "libnvrtc.so.11.0.194",
]

WINDOWS_FILES = [
    "embree3.dll", "tbb12.dll", "tbb.dll", "tbbmalloc.dll",
    "pyluxcore.pyd",
    "pyluxcoretool.exe", "pyluxcoretools.zip",
    "OpenImageDenoise.dll", "oidnDenoise.exe",
    "nvrtc64_101_0.dll", "nvrtc-builtins64_101.dll",
]

MAC_FILES = [
    "libembree3.3.dylib", "libomp.dylib",
    "libOpenImageDenoise.1.4.0.dylib", "libOpenImageIO.2.2.dylib",
    "libcuda.dylib", "libnvrtc.dylib",
    "libtbb.dylib", "libtbbmalloc.dylib", "libtiff.5.dylib",
    "pyluxcore.so", "pyluxcoretools.zip", "oidnDenoise"
]

# On Windows and macOS, OIDN is downloaded by the LuxCore build script
OIDN_LINUX = "oidn-linux.tar.gz"
OIDN_LINUX_URL = "https://github.com/OpenImageDenoise/oidn/releases/download/v1.3.0/oidn-1.3.0.x86_64.linux.tar.gz"


def print_divider():
    print("=" * 60)


def build_name(prefix, version_string, suffix):
    return prefix + version_string + suffix


def build_zip_name(version_string, suffix):
    suffix_without_extension = suffix.replace(".tar.bz2", "").replace(".zip", "").replace(".tar.gz", "").replace(".dmg", "")
    return f"BlendLuxCore-{version_string}{suffix_without_extension}"


def extract_files_from_tar(tar_path, files_to_extract, destination):
    # have to use a temp dir (weird extract behaviour)
    temp_dir = os.path.join(script_dir, "temp")
    # Make sure we don't delete someone's beloved temp folder later
    while os.path.exists(temp_dir):
        temp_dir += "_"
    os.mkdir(temp_dir)

    print("Reading tar file:", tar_path)

    tar_type = os.path.splitext(tar_path)[1][1:]
    with tarfile.open(tar_path, f"r:{tar_type}") as tar:
        for member in tar.getmembers():
            basename = os.path.basename(member.name)
            if basename not in files_to_extract:
                continue

            # have to use a temp dir (weird extract behaviour)
            print(f'Extracting "{basename}" to "{temp_dir}"')
            tar.extract(member, path=temp_dir)
            src = os.path.join(temp_dir, member.name)

            # move to real target directory
            dst = os.path.join(destination, basename)
            print(f'Moving "{src}" to "{dst}"')
            if not os.path.isfile(dst):
                shutil.move(src, dst)

    rmtree(temp_dir)


def extract_files_from_zip(zip_path, files_to_extract, destination):
    # have to use a temp dir (weird extract behaviour)
    temp_dir = os.path.join(script_dir, "temp")
    # Make sure we don't delete someone's beloved temp folder later
    while os.path.exists(temp_dir):
        temp_dir += "_"
    os.mkdir(temp_dir)

    print("Reading zip file:", zip_path)

    with zipfile.ZipFile(zip_path, "r") as zip:
        for member in zip.namelist():
            basename = os.path.basename(member)  # in zip case, member is just a string
            if basename not in files_to_extract:
                continue

            # have to use a temp dir (weird extract behaviour)
            print(f'Extracting "{basename}" to "{temp_dir}"')
            src = zip.extract(member, path=temp_dir)

            # move to real target directory
            dst = os.path.join(destination, basename)
            print(f'Moving "{src}" to "{dst}"')
            shutil.move(src, dst)

    rmtree(temp_dir)


def extract_files_from_dmg(dmg_path, files_to_extract, destination):

    print("Extracting dmg file:", dmg_path)
    vol_name = dmg_path.replace(".dmg", "")
    for f in files_to_extract:
        print(f'Extracting "{f}" to "{destination}"')
        cmd = f"7z e -o{destination} {dmg_path} {vol_name}/pyluxcore/{f}"
        print(cmd)
        os.system(cmd)


def extract_files_from_archive(archive_path, files_to_extract, destination):
    if archive_path.endswith(".zip"):
        extract_files_from_zip(archive_path, files_to_extract, destination)
    elif archive_path.endswith(".tar.gz") or archive_path.endswith(".tar.bz2"):
        extract_files_from_tar(archive_path, files_to_extract, destination)
    elif archive_path.endswith(".dmg"):
        extract_files_from_dmg(archive_path, files_to_extract, destination)
    else:
        raise Exception("Unknown archive type:", archive_path)


def extract_luxcore_tar(prefix, platform_suffixes, file_names, version_string):
    for suffix in platform_suffixes:
        dst_name = build_zip_name(version_string, suffix)
        destination = os.path.join(script_dir, dst_name, "BlendLuxCore", "bin")

        print()
        print_divider()
        print("Extracting tar to", dst_name)
        print_divider()

        tar_name = build_name(prefix, version_string, suffix)
        extract_files_from_archive(tar_name, file_names, destination)

def extract_luxcore_dmg(prefix, platform_suffixes, file_names, version_string):
    for suffix in platform_suffixes:
        dst_name = build_zip_name(version_string, suffix)
        destination = os.path.join(script_dir, dst_name, "BlendLuxCore", "bin")

        print()
        print_divider()
        print("Extracting dmg to", dst_name)
        print_divider()

        dmg_name = build_name(prefix, version_string, suffix)
        extract_files_from_dmg(dmg_name, file_names, destination)


def extract_luxcore_zip(prefix, platform_suffixes, file_names, version_string):
    for suffix in platform_suffixes:
        dst_name = build_zip_name(version_string, suffix)
        destination = os.path.join(script_dir, dst_name, "BlendLuxCore", "bin")

        print()
        print_divider()
        print("Extracting zip to", dst_name)
        print_divider()

        zip_name = build_name(prefix, version_string, suffix)
        extract_files_from_archive(zip_name, file_names, destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("version_string",
                        help='E.g. "v2.0alpha1" or "v2.0". Used to download the LuxCore zips')
    args = parser.parse_args()

    # Archives we need.
    if args.version_string == "latest":
        url_prefix = "https://github.com/LuxCoreRender/LuxCore/releases/download/"
    else:
        url_prefix = "https://github.com/LuxCoreRender/LuxCore/releases/download/luxcorerender_"
    prefix = "luxcorerender-"
    suffixes = [
        "-linux64.tar.bz2",
        # "-linux64-opencl.tar.bz2",
        # "-linux64-cuda.tar.bz2",  # TODO need to find out how to include the necessary CUDA files
        "-win64.zip",
        "-mac64.dmg"
    ]

    # Download LuxCore binaries for all platforms
    print_divider()
    print("Downloading LuxCore releases")
    print_divider()

    to_remove = []
    for suffix in suffixes:
        name = build_name(prefix, args.version_string, suffix)

        # Check if file already downloaded
        if name in os.listdir(script_dir):
            print(f'File already downloaded: "{name}"')
        else:
            destination = os.path.join(script_dir, name)
            url = url_prefix + args.version_string + "/" + name
            print(f'Downloading: "{url}"')

            try:
                urllib.request.urlretrieve(url, destination)
            except urllib.error.HTTPError as error:
                print(error)
                print("Archive", name, "not available, skipping it.")
                to_remove.append(suffix)

    # Remove suffixes that were not available for download
    for suffix in to_remove:
        suffixes.remove(suffix)

    print()
    print_divider()
    print("Cloning BlendLuxCore")
    print_divider()

    # Clone BlendLuxCore (will later put the binaries in there)
    repo_path = os.path.join(script_dir, "BlendLuxCore")
    if os.path.exists(repo_path):
        # Clone fresh because we delete some stuff after cloning
        print(f'Destinaton already exists, deleting it: "{repo_path}"')
        rmtree(repo_path)

    clone_args = ["git", "clone", "https://github.com/LuxCoreRender/BlendLuxCore.git"]
    git_process = subprocess.Popen(clone_args)
    git_process.wait()

    # If the current version tag already exists, set the repository to this version
    # This is used in case we re-package a release
    os.chdir("BlendLuxCore")

    print("Checking out master")
    subprocess.check_output(["git", "checkout", "master"])

    tags_raw = subprocess.check_output(["git", "tag", "-l"])
    tags = [tag.decode("utf-8") for tag in tags_raw.splitlines()]

    current_version_tag = f"blendluxcore_{args.version_string}"
    if current_version_tag in tags:
        print("Checking out tag", current_version_tag)
        subprocess.check_output(["git", "checkout", f"tags/{current_version_tag}"])

    os.chdir("..")

    # Delete developer stuff that is not needed by users
    to_delete = [
        os.path.join(repo_path, "doc"),
        os.path.join(repo_path, ".github"),
        os.path.join(repo_path, ".git"),
        os.path.join(repo_path, "scripts"),
    ]
    for path in to_delete:
        rmtree(path)

    print()
    print_divider()
    print("Creating BlendLuxCore release subdirectories")
    print_divider()

    # Create subdirectories for all platforms
    for suffix in suffixes:
        name = build_zip_name(args.version_string, suffix)
        destination = os.path.join(script_dir, name, "BlendLuxCore")
        print(f'Creating "{destination}"')

        if os.path.exists(destination):
            print("(Already exists, cleaning it)")
            rmtree(destination)

        shutil.copytree(repo_path, destination)

    print()
    print_divider()
    print("Downloading OIDN binaries")
    print_divider()

    # Check if file already downloaded
    if OIDN_LINUX in os.listdir(script_dir):
        print(f'File already downloaded: "{OIDN_LINUX}"')
    else:
        destination = os.path.join(script_dir, OIDN_LINUX)
        try:
            urllib.request.urlretrieve(OIDN_LINUX_URL, destination)
        except urllib.error.HTTPError as error:
            print(error)

    print("Extracting OIDN standalone denoiser")

    for suffix in suffixes:
        name = build_zip_name(args.version_string, suffix)
        destination = os.path.join(script_dir, name, "BlendLuxCore", "bin")

        # On Windows and macOS, OIDN is downloaded by the LuxCore build script, so we don't need to do it here
        #if "win64" in suffix:
        #    extract_files_from_archive(OIDN_WIN, ["oidnDenoise.exe"], destination)
        if "linux64" in suffix:
            extract_files_from_archive(OIDN_LINUX, ["oidnDenoise"], destination)
        #elif "mac64" in suffix:
        #    extract_files_from_archive(OIDN_MAC, ["oidnDenoise"], destination)

    # Linux archives are tar.bz2
    linux_suffixes = [suffix for suffix in suffixes if "-linux" in suffix]
    extract_luxcore_tar(prefix, linux_suffixes, LINUX_FILES, args.version_string)

    # Mac archives are dmg
    mac_suffixes = [suffix for suffix in suffixes if "-mac" in suffix]
    extract_luxcore_dmg(prefix, mac_suffixes, MAC_FILES, args.version_string)

    # Windows archives are zip
    windows_suffixes = [suffix for suffix in suffixes if "-win" in suffix]
    extract_luxcore_zip(prefix, windows_suffixes, WINDOWS_FILES, args.version_string)

    # Package everything
    print()
    print_divider()
    print("Packaging BlendLuxCore releases")
    print_divider()

    release_dir = os.path.join(script_dir, f"release-{args.version_string}")
    if os.path.exists(release_dir):
        rmtree(release_dir)
    os.mkdir(release_dir)

    for suffix in suffixes:
        name = build_zip_name(args.version_string, suffix)
        zip_this = os.path.join(script_dir, name)
        print("Zipping:", name)
        zip_name = f"{name}.zip"

        shutil.make_archive(name, 'zip', zip_this)

        shutil.move(f"{zip_this}.zip", os.path.join(release_dir, zip_name))

    print()
    print_divider()
    print(f"Results can be found in: {release_dir}")
    print_divider()


if __name__ == "__main__":
    main()
