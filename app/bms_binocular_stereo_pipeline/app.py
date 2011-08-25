"""
Binocular Stereo Pipeline
"""

from lib import base_app, image, build, http
from lib.misc import app_expose, ctime, gzip
from lib.base_app import init_app
from cherrypy import TimeoutError
import os.path
import time
import cherrypy
import shutil

#
# INTERACTION
#

class NoMatchError(RuntimeError):
    pass

class app(base_app):
    """ template demo app """
    
    title = "Binocular Stereo Pipeline"

    input_nb = 2 # number of input images
    input_max_pixels = 640 * 480 # max size (in pixels) of an input image
    input_dtype = '3x8i' # input image expected data type
    input_ext = '.png'   # input image expected extension (ie file format)    
    is_test = False      # switch to False for deployment

    def __init__(self):
        """
        app setup
        """
        # setup the parent class
        base_dir = os.path.dirname(os.path.abspath(__file__))
        base_app.__init__(self, base_dir)

        # select the base_app steps to expose
        # index() is generic
        app_expose(base_app.index)

    def build(self):
        """
        program build/update
        """
        # store common file path in variables
        tgz_file = self.dl_dir + "MissStereo.tar.gz"
        tgz_url = "http://www.ipol.im/pub/algo/" + \
            "m_quasi_euclidean_epipolar_rectification/MissStereo.tar.gz"
        build_dir = (self.src_dir + os.path.join("MissStereo", "build")
                     + os.path.sep)
        src_bin = dict([(build_dir + os.path.join("bin", prog),
                         self.bin_dir + prog)
                        for prog in ["homography", "orsa", "rectify",
                                     "sift", "size", "stereoAC",
                                     "selfSimilar", "subPixel", "medianFill",
                                     "convert", "mesh", "density"]])
        src_bin[self.src_dir
                + os.path.join("MissStereo",
                               "scripts", "MissStereo.sh")] \
                               = os.path.join(self.bin_dir, "MissStereo.sh")
        log_file = self.base_dir + "build.log"
        # get the latest source archive
        build.download(tgz_url, tgz_file)
        # test if any of the dest files is missing, or too old
        if all([(os.path.isfile(bin_file) and ctime(tgz_file) < ctime(bin_file))
                for bin_file in src_bin.values()]):
            cherrypy.log("not rebuild needed",
                         context='BUILD', traceback=False)
        else:
            # extract the archive
            build.extract(tgz_file, self.src_dir)
            # build the program
            os.mkdir(build_dir)
            build.run("cmake -D CMAKE_BUILD_TYPE:string=Release ../src",
                      stdout=log_file, cwd=build_dir)
            build.run("make -C %s -j4" % build_dir,
                      stdout=log_file)
            # save into bin dir
            if os.path.isdir(self.bin_dir):
                shutil.rmtree(self.bin_dir)
            os.mkdir(self.bin_dir)
            for (src, dst) in src_bin.items():
                shutil.copy(src, dst)
            # cleanup the source dir
            shutil.rmtree(self.src_dir)
        return

    #
    # PARAMETER HANDLING
    #

    @cherrypy.expose
    @init_app
    def params(self, newrun=False, msg=None):
        """
        configure the algo execution
        """
        if newrun:
            self.clone_input()
        if (image(self.work_dir + 'input_0.png').size
            != image(self.work_dir + 'input_1.png').size):
            return self.error('badparams',
                              "The images must have the same size")
        return self.tmpl_out("params.html")

    @cherrypy.expose
    @init_app
    def wait(self, **kwargs):
        """
        params handling and run redirection
        """
        http.refresh(self.base_url + 'run?key=%s' % self.key)
        return self.tmpl_out("wait.html",
                             height=image(self.work_dir
                                          + 'input_0.png').size[1])

    @cherrypy.expose
    @init_app
    def run(self, **kwargs):
        """
        algorithm execution
        """
        try:
            run_time = time.time()
            self.run_algo(timeout=self.timeout)
            self.cfg['info']['run_time'] = time.time() - run_time
            self.cfg.save()
        except TimeoutError:
            return self.error(errcode='timeout',
                              errmsg="Try again with simpler images.")
        except NoMatchError:
            http.redir_303(self.base_url + 'result?key=%s&error_nomatch=1' % self.key)
        except RuntimeError:
            return self.error(errcode='runtime')
        else:
            http.redir_303(self.base_url + 'result?key=%s' % self.key)

            # archive
            if self.cfg['meta']['original']:
                ar = self.make_archive()
                ar.add_file("input_0.orig.png", info="uploaded #1")
                ar.add_file("input_1.orig.png", info="uploaded #2")
                ar.add_file("input_0.png", info="input #1")
                ar.add_file("input_1.png", info="input #2")
                ar.add_file("rect_0.png", info="rectified #1")
                ar.add_file("rect_1.png", info="rectified #1")
                ar.add_file("disp1_0.png", info="AC pixel disparity")
                ar.add_file("disp2_0.png", info="self-sim. filter disp.")
                ar.add_file("disp3_0.png", info="sub-pixel disparity")
                ar.add_file("disp4_0.png", info="denser disparity")
                f = open(self.work_dir + 'homo_0.txt')
                ar.add_info({"homography #1" : f.readline()})
                f.close()
                f = open(self.work_dir + 'homo_1.txt')
                ar.add_info({"homography #2" : f.readline()})
                f.close()
                ar.add_file("orsa.txt.gz")
                ar.add_file("disp4_0.ply.gz")
                ar.save()

        return self.tmpl_out("run.html")


    def run_algo(self, timeout=None):
        """
        the core algo runner
        could also be called by a batch processor
        this one needs no parameter
        """
        # run Rectify.sh
        stdout = open(self.work_dir + 'stdout.txt', 'w')
        p = self.run_proc(['MissStereo.sh',
                           self.work_dir + 'input_0.png',
                           self.work_dir + 'input_1.png'],
                          stdout=stdout, stderr=stdout)
        try:
            self.wait_proc(p, timeout)
        except RuntimeError:
            if 0 != p.returncode:
                stdout.close()
                raise NoMatchError
            else:
                raise
        stdout.close()

        mv_map = {'input_0.png_input_1.png_pairs_orsa.txt' : 'orsa.txt',
                  'input_0.png_h.txt' : 'homo_0.txt',
                  'input_1.png_h.txt' : 'homo_1.txt',
                  'disp1_H_input_0.png.png' : 'disp1_0.png',
                  'disp2_H_input_0.png.png' : 'disp2_0.png',
                  'disp3_H_input_0.png.png' : 'disp3_0.png',
                  'disp4_H_input_0.png.png' : 'disp4_0.png',
                  'disp1_H_input_0.png_float.tif' : 'disp1_0.tif',
                  'disp2_H_input_0.png_float.tif' : 'disp2_0.tif',
                  'disp3_H_input_0.png_float.tif' : 'disp3_0.tif',
                  'disp4_H_input_0.png_float.tif' : 'disp4_0.tif',
                  'disp4_H_input_0.png.ply' : 'disp4_0.ply',
                  'H_input_0.png' : 'rect_0.png',
                  'H_input_1.png' : 'rect_1.png'}
        for (src, dst) in mv_map.items():
            shutil.move(self.work_dir + src, self.work_dir + dst)
        gzip(self.work_dir + 'orsa.txt')
        gzip(self.work_dir + 'disp4_0.ply')

        return

    @cherrypy.expose
    @init_app
    def result(self, error_nomatch=None):
        """
        display the algo results
        """
        if error_nomatch:
            return self.tmpl_out("result_nomatch.html")
        else:
            return self.tmpl_out("result.html",
                                 height=image(self.work_dir
                                              + 'input_0.png').size[1])
