'''
Created on Dec 20, 2021

@author: Kanehekili
The code is based on 
https://github.com/jaseg/python-mpv
under the
GNU Affero General Public License v3.0
'''
     
from PyQt5.QtCore import Qt
from PyQt5 import QtCore,QtWidgets
from PyQt5 import QtGui
from PyQt5.QtWidgets import QApplication

from threading import Condition
from PyQt5.QtCore import pyqtSignal
import FFMPEGTools
import sys,time

try:
    from PIL.ImageQt import ImageQt #Not there by default...
except ImportError:
    print ("PIL lib not found")
    app = QApplication(sys.argv)
    QtWidgets.QMessageBox.critical(None, "PIL lib","python PIL must be installed to run VideoCut.")
    sys.exit(1)    

try:
    from lib.mpv import MPV
except ImportError:
    print (("MPV lib not found"))  
    app = QApplication(sys.argv)
    QtWidgets.QMessageBox.critical(None, "MPV lib","libmpv must be installed to run VideoCut.")
    sys.exit(1)    

Log=FFMPEGTools.Log

class VideoWidget(QtWidgets.QFrame):
    """ Sized frame for mpv """
    trigger = pyqtSignal(float,float,float)
    
    def __init__(self, parent):
        QtWidgets.QFrame.__init__(self, parent)
        self._defaultHeight = 518 #ratio 16:9
        self._defaultWidth = 921
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setFrameStyle(QtWidgets.QFrame.Panel | QtWidgets.QFrame.Sunken)
        self.setLineWidth(1)

    def sizeHint(self):
        return QtCore.QSize(self._defaultWidth, self._defaultHeight)

    def updateUI(self,frameNumber,framecount,timeinfo):
        self.trigger.emit(frameNumber,framecount,timeinfo)

class MpvPlayer():
    def __init__(self):
        self.mediaPlayer =None
        self.framecount=None
        self.fps=25.0
        self.duration=0.0
        self.seekLock=Condition()
        self._frameInfoFunc=None
        self._timePos=0.0
        self._demuxOffset=0.1
        self.isReadable=False
        self.play_func=None
        self._lastDispatch=0.0
    
    def initPlayer(self,container):
        self.mediaPlayer = MPV(wid=str(int(container.winId())),#these are all options, can be accessed with mediaPlayer[option] (props with mp.prop)
            #vo='x11', # You may not need this -vo=gpu is default
            log_handler=self._passLog,
            loglevel='error',
            input_vo_keyboard=False,  #We'll take the qt events
            pause=True,
            mute='yes',
            keep_open="always",
            #rebase_start_time='yes',  #default, no will show the real time
            #hr_seek_framedrop='yes',  #default, no=still no backwards seek on mts
            #stream_buffer_size='256MiB',#Works for uhd (dimensions>1920xx)
            hr_seek='yes',            #yes ok on slider search
            #deinterlace="yes",        #needed for m2t if interlaced...
            #index="recreate",         #test for m2t
            #demuxer_lavf_probescore=100, #not working with mpg
            demuxer_lavf_analyzeduration=100.0,
            #demuxer_backward_playback_step=1024, #no help
            #video_sync="display-desync", #no help
            hr_seek_framedrop="no",
            #demuxer_lavf_probesize=1000, #won't load any mpg2 stream
            #demuxer_lavf_probe_info='yes',#not working on m2ts either
            hr_seek_demuxer_offset=self._demuxOffset, #offset too large (2.1) will slow everything down, only if hr_seek is true
            #video_aspect_override="16:9, #works,but not necessary
            #demuxer_lavf_hacks='yes', #test for m2t -no won't help 
            #the follwing entries enable mts back seeking: (https://github.com/mpv-player/mpv/issues/4019#issuecomment-747853186)
            cache='yes',
            demuxer_seekable_cache='yes',
            demuxer_max_back_bytes ='10000MiB',
            #demuxer_max_bytes ='10MiB',
            #demuxer_backward_playback_step=180,
            demuxer_cache_wait='no', #if yes remote files take too long...
            volume=100
         ) 
        
        self._hookEvents()
        return self.mediaPlayer 
    
    def _passLog(self,loglevel, component, message):
        msg='{}: {}'.format(component, message)
        Log.logError(msg)
    
    def open(self,filePath):
        try:     
            #self.mediaPlayer.register_event_callback(self._oncallback)
            self.mediaPlayer.loadfile(filePath)
            self._getReady()
            print("player init")
        except Exception as ex:
            Log.logException("Open mpv file")
            print(ex)
    
    def _oncallback(self,callback):
        print("callback:",callback)
        
    def close(self):
        if self.mediaPlayer:
            self.mediaPlayer.quit()
        
    def getCurrentFrameNumber(self):
        return round(self._timePos*self.fps,0)
    
    def validate(self):
        pass #ffmpeg can read it ..

    #take the current time and add/subtract a number of frames and return the "absolute" new time
    def calcOffset(self,frameOffset):
        nxt=self._timePos+(frameOffset/self.fps)
        return nxt

    def calcPosition(self,frameNumber):
        return frameNumber/self.fps
  
    def seek(self,frameNumber):
        if self.mediaPlayer.seeking is None:
            Log.logError("No seek! Aborting")
            return
        step = frameNumber - self.getCurrentFrameNumber()
        if abs(step) < 20: #mpv hack: mpegts small distances
            #self.__seekPrecise(step)
            self.seekStep(step)
            return
        secs = self.calcPosition(frameNumber)
        tp = self.timePos()
        #print("seek to pos: %f [%f] fn:%d currFn:%d"%(secs,tp,frameNumber,self.getCurrentFrameNumber()))
        self.mediaPlayer.seek(secs,"absolute+exact")
        self._waitSeekDone()
        #print("seek pos now:%f fn:%d"%(self.timePos(),self.getCurrentFrameNumber()))
    
    #unused    
    def __seekPrecise(self,dialStep):
        secs=self.calcOffset(dialStep)
        #print("dial:",dialStep)
        self.mediaPlayer.seek(secs,"absolute+exact")
        self._waitSeekDone()
        #self.mediaPlayer.hr_seek_demuxer_offset=0.0

    #using dialStep with relative leads to different timestamps... 
    def seekStep(self,dialStep):
        if self.mediaPlayer.seeking is not None:
            #Log.logInfo("Seek step %d"%(dialStep))
            if dialStep > 0:
                #self.mediaPlayer.frame_step() #crash at end/fills queue with afterruns
                fix=0.8
                if dialStep > 3:
                    fix=1.0
                nxt=(dialStep/self.fps)*fix
                if self.timePos()+nxt>self.duration:
                    return
            else:
                #too slow: self.mediaPlayer.frame_back_step()
                if self.timePos()>self.duration:
                    nxt=-1/self.fps*1.8
                else:
                    nxt=(dialStep/self.fps)*1.2
            self.mediaPlayer.seek(nxt,"relative+exact")
            #print("seek step1 %f time:%f dur:%f"%(nxt,self.timePos(),self.duration)) 
            self._waitSeekDone()
            #print("seekStep2 %f dial: %d currTime:%f"%(nxt,dialStep,self.timePos()))
        else:
            Log.logInfo("MPV: Seek none!")
                               
    def screenshotAtFrame(self,frameNumber):
        secs = self.calcPosition(frameNumber)
        self.mediaPlayer.seek(secs,"absolute+exact") #this works only, if seeking is done, otherwise crash.
        self._waitSeekDone()
        return self.screenshotImage()
    
    def screenshotImage(self):
        im=self.mediaPlayer.screenshot_raw(includes="video")
        return ImageQt(im)#scale? ==QImage        

    def takeScreenShot(self,path):
        self.mediaPlayer.screenshot_to_file(path,includes="video")
        return True
        
    def _hookEvents(self):
        observe=[]#"seeking","time-pos"...
        #observe = self.mediaPlayer.property_list
        for prop in observe:
            self.mediaPlayer.observe_property(prop, self._onPropertyChange)
            
        self.mediaPlayer.observe_property("time-pos",self._onTimePos) #messes up timing!
        self.mediaPlayer.observe_property("eof-reached",self._onPlayEnd)
        #self.mediaPlayer.observe_property("estimated-vf-fps",self._onFps)    
        #mostly wrong: self.mediaPlayer.observe_property("estimated-frame-count",self._onFramecount)
        self.mediaPlayer.observe_property("duration",self._onDuration)
        self.mediaPlayer.observe_property("video-frame-info",self._onFrameInfo)
            
    def _onPropertyChange(self,name,pos):
        print("        >",name,":",pos)
    
    def _onDuration(self,name,val):
        if val is not None:
            self.duration=val
            Log.logInfo("durance detected:%.3f"%(val))
        
    def _onFrameInfo(self,name,val):
        if val is not None:
            #video-frame-info : {'picture-type': 'I', 'interlaced': False, 'tff': False, 'repeat': False}
            self.mediaPlayer.show_text(val['picture-type'],'0x7FFFFFFF') #32bit max
    
    def setFPS(self,newFPS):
        self.fps=newFPS
        self.framecount=self.duration*newFPS #framecount prop not reliable
        #often a difference between the mpv fps and the fmmpeg fps
        self.mediaPlayer["fps"]=newFPS
    
    '''            
    def _onFps(self,name,val):
        if val is not None:
            Log.logInfo("fps detected: %.5f"%(val))
            self.mediaPlayer.unobserve_property("estimated-vf-fps",self._onFps)
            self.setFPS(val)
    '''
    
    def _onTimePos(self,name,val):
        if val is not None:
            self._timePos=val
            if not self._frameInfoFunc:
                return

            if not self.mediaPlayer.pause:  #player hack...          
                now=time.monotonic()
                if now-self._lastDispatch < (1/self.fps):
                    return
                self._lastDispatch=now
            frameNumber=self.getCurrentFrameNumber()
            '''
            xfps = self.fps
            if xfps is None:
                xfps=-1.0
            xeps= self.mediaPlayer.estimated_vf_fps
            if xeps is None:
                xeps=-1.0
            print("prop time %.3f fps:%f fn:%d fc:%d"%(val,xfps,frameNumber,self.framecount))
            '''
            self._frameInfoFunc(frameNumber,self.framecount,self.timePos()*1000)
    
    def _onPlayEnd(self,name,val):
        if val == True:
            self.play_func(False)
            
    def _onSeek(self,name,val):
        if val==False:
            with self.seekLock:
                self.seekLock.notify()
                self.mediaPlayer.unobserve_property("seeking",self._onSeek)
    
    def _waitSeekDone(self):
        self.mediaPlayer.observe_property("seeking",self._onSeek)
        with self.seekLock:  
            self.seekLock.wait(timeout=3)
            
            
    def _getReady(self):
        self.mediaPlayer.observe_property("estimated-vf-fps", self._onReadyWait)
        with self.seekLock:  
            self.isReadable=self.seekLock.wait(timeout=15)
    
    def _onReadyWait(self,name,val):
        if val is not None:
            with self.seekLock:
                    self.mediaPlayer.unobserve_property("estimated-vf-fps",self._onReadyWait)
                    Log.logInfo("fps detected: %.5f"%(val))
                    self.setFPS(val)
                    self.seekLock.notify()
                    
                    
                
    def timePos(self):
        return self._timePos+1/self.fps
       
    def isValid(self):
        return self.mediaPlayer.seekable
    
    def connectTo(self,func):
        self._frameInfoFunc=func
    
    #hack for transport streams
    def tweakTansportStreamSettings(self,isInterlaced):
        Log.logInfo("Transport stream. Setting seek offset to 1.5 and interlacing: %d"%(isInterlaced))
        self.mediaPlayer.hr_seek_demuxer_offset=1.5#Solution for mpegts seek
        if isInterlaced:
            self.mediaPlayer.deinterlace="yes"
 
    def tweakUHD(self):
        Log.logInfo("UHD, set stream size")
        self.mediaPlayer.stream_buffer_size='256MiB' 
        #self.mediaPlayer.hr_seek_demuxer_offset=2.5 
        #self.mediaPlayer.demuxer_backward_playback_step=1024        
        #self.mediaPlayer.demuxer_lavf_probescore=100   
        #self.mediaPlayer.demuxer_lavf_probesize=1000
        #self.mediaPlayer.demuxer_lavf_probe_info='yes'
          
 
    def mpvVersion(self):
        return self.mediaPlayer.mpv_version
 
    #relevant if we reach end while playing
    def syncPlay(self,func):
        self.play_func=func
        
    def syncToStart(self):
        self._onTimePos("timepos", self.timePos())

class MpvPlugin():
    def __init__(self,iconSize):
        self.mpvWidget=None
        self.player=None
        self.iconSize=iconSize
        self.controller=None #VCControl
    
    def initPlayer(self,filePath, streamData):
        import locale
        locale.setlocale(locale.LC_NUMERIC, 'C')
        if self.player:
            self.player.close()
        self.player= MpvPlayer()
        self.player.initPlayer(self.mpvWidget)
        self.player.open(filePath)    
        if not self.player.isReadable:
            raise Exception("Invalid file")
        self._sanityCheck(streamData)
        self.player.connectTo(self.mpvWidget.updateUI)
        self.player.syncPlay(self.markStopPlay)
        return self.player

    def validate(self):
        if self.player:
            return self.player.validate()
        raise Exception('Invalid file')       
    
    def closePlayer(self):
        if self.player:
            self.player.close()      
    
    def createWidget(self,parent):
        self.mpvWidget=VideoWidget(parent)
        self.mpvWidget.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.mpvWidget.setAttribute(Qt.WA_NativeWindow)    
        return self.mpvWidget
    
    def videoWidget(self):
        return self.mpvWidget
    
    def setCutEntry(self,cutEntry,restore=False): #this is a cv restore hack
        if restore: #legacy: create a pix from old entry 
            cutEntry.frameNumber=cutEntry.frameNumber-1 #cv compensation
            pilImage = self.player.screenshotAtFrame(cutEntry.frameNumber)
        else: #create a new one
            pilImage = self.player.screenshotImage()

            #set: cutEntry.frameNumber=self.player.getCurrentFrameNumber()    
        cutEntry.timePosMS=self.player.timePos()*1000 #Beware +1!
        cutEntry.pix = self._makeThumbnail(pilImage)
    
    def _makeThumbnail(self,qImage):
        pix = QtGui.QPixmap.fromImage(qImage)
        pix = pix.scaledToWidth(self.iconSize, mode=Qt.SmoothTransformation)
        return pix       

    def _sanityCheck(self,streamData):
        if streamData is None:
            return
        duration = streamData.formatInfo.getDuration()
        videoInfo = streamData.getVideoStream()               
        ff_fps= videoInfo.frameRateMultiple()
        ff_FrameCount = round(ff_fps*duration)
        isUHD = float(videoInfo.getWidth())>3000.0
        interlaced = videoInfo.isInterlaced()
        #rot = streamData.getRotation()
        #ratio = streamData.getAspectRatio()
        Log.logInfo("Analyze MPV frameCount:%d fps:%.3f /FFMPEG frameCount:%d fps:%.3f, interlaced:%d"%(self.player.framecount,self.player.fps,ff_FrameCount,ff_fps,interlaced))   
        
        fps_check= abs((self.player.fps/ff_fps)-1)
        #if fps_check >0.1:
        Log.logInfo("Setting FPS into MPV, ratio: %.3f setting fps %.3f"%(fps_check,ff_fps))
        self.player.setFPS(ff_fps)
            
        fcCheck= (self.player.framecount/ff_FrameCount)
        if fcCheck < 0.9 or fcCheck > 1.1:
            Log.logInfo("Irregular count, ratio: %.3f, setting framecount %d"%(fcCheck,ff_FrameCount))
            self.player.framecount=ff_FrameCount    
            
        #Transport stream handling:
        if streamData.isTransportStream():
            self.player.tweakTansportStreamSettings(interlaced)  
        if isUHD:
            self.player.tweakUHD()   

    def showBanner(self):
        self.initPlayer("icons/film-clapper.png",None)
        #self._showPos()
        
    def showFirstFrame(self):
        self.player.syncToStart()
        #self._showPos()

    #slider    
    def enqueueFrame(self,fn): #Slider stuff
        self.player.seek(fn)
        #self._showPos()

    #spinbutton    
    def setFrameDirect(self,frameNumber):
        self.player.seek(frameNumber)
        #self.player.updateInfo(frameNumber) #intended hack for spin button
                
        
    #dial
    def onDial(self,pos):
        self.player.seekStep(pos)
        #self._showPos()

    def toggleVideoPlay(self):
       
        playing = self.player.mediaPlayer.pause #property
        if playing:
            self.player.mediaPlayer["mute"]="no" #option
        else:
            self.player.mediaPlayer["mute"]="yes"
        self.player.mediaPlayer.pause=not playing
        return playing
    
    def markStopPlay(self,boolval):
        #MPVEventHandlerThread-pass it over to the main thread.
        #correct: self.mpvWidget.triggerPlayerControls(boolval)
        self.controller.syncVideoPlayerControls(boolval)
        
    

    '''
    def _showPos(self):
        self.player.updateInfo()
    '''    