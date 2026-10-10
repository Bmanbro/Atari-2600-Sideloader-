/* Pac-Man 2600+ game selector. Uses the firmware's SDL2 and Linux joystick ABI.
 * The selector writes only a validated content hash; the launcher validates
 * the chosen ROM again. No catalog text is executed as a command.
 */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define LIMIT 128
#define ROWS 6
#define IDLE_REDRAW_MS 100
#define KEYS "/sys/devices/platform/gpio-keys-polled/keys"
typedef struct { int x, y, w, h; } Rect;
typedef struct { uint32_t time; int16_t value; uint8_t type, number; } JoyEvent;
_Static_assert(sizeof(JoyEvent) == 8, "Linux joystick event ABI");
typedef struct { char id[65], title[97]; int system; } Game;
typedef struct { int fd, axis, port; unsigned logged_buttons, logged_axes; } Pad;
static Game games[LIMIT];
static int count;
static volatile sig_atomic_t stopped;
static void *window, *renderer, *library;

/* Minimal declarations of stable SDL2 functions; no vendor headers or binaries
 * are redistributed. Opaque SDL handles are never dereferenced here. */
static int (*Init)(uint32_t), (*SetHint)(const char *, const char *);
static const char *(*GetError)(void);
static void *(*CreateWindow)(const char *, int, int, int, int, uint32_t);
static void *(*CreateRenderer)(void *, int, uint32_t);
static int (*SetRenderDrawColor)(void *, uint8_t, uint8_t, uint8_t, uint8_t);
static int (*RenderClear)(void *), (*RenderFillRect)(void *, const Rect *);
static int (*RenderSetLogicalSize)(void *, int, int);
static void (*RenderPresent)(void *), (*DestroyRenderer)(void *);
static void (*DestroyWindow)(void *), (*Quit)(void), (*Delay)(uint32_t);
static void (*PumpEvents)(void);
static uint32_t (*GetTicks)(void);

/* Five-column lettering, drawn as rectangles at integer scale. */
static const uint8_t letters[26][7] = {
 {14,17,17,31,17,17,17},{30,17,17,30,17,17,30},{14,17,16,16,16,17,14},
 {30,17,17,17,17,17,30},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
 {14,17,16,23,17,17,15},{17,17,17,31,17,17,17},{31,4,4,4,4,4,31},
 {7,2,2,2,18,18,12},{17,18,20,24,20,18,17},{16,16,16,16,16,16,31},
 {17,27,21,21,17,17,17},{17,25,21,19,17,17,17},{14,17,17,17,17,17,14},
 {30,17,17,30,16,16,16},{14,17,17,17,21,18,13},{30,17,17,30,20,18,17},
 {15,16,16,14,1,1,30},{31,4,4,4,4,4,4},{17,17,17,17,17,17,14},
 {17,17,17,17,17,10,4},{17,17,17,21,21,21,10},{17,17,10,4,10,17,17},
 {17,17,10,4,4,4,4},{31,1,2,4,8,16,31}
};
static const uint8_t numbers[10][7] = {
 {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
 {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
 {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},
 {14,17,17,15,1,1,14}
};
static uint8_t glyph(char c, int row) {
 c=(char)toupper((unsigned char)c);
 if(c>='A' && c<='Z') return letters[c-'A'][row];
 if(c>='0' && c<='9') return numbers[c-'0'][row];
 switch(c) {
 case ' ': return 0;
 case '.': return row==6?4:0;
 case ',': return row==5?4:row==6?8:0;
 case '-': return row==3?14:0;
 case '_': return row==6?31:0;
 case ':': return row==2 || row==5?4:0;
 case '/': return (uint8_t)(1u << (row*4/6));
 case '!': return row<4 || row==6?4:0;
 case '\'': return row<2?4:0;
 case '(': return row==0 || row==6?2:4;
 case ')': return row==0 || row==6?8:4;
 case '+': return row==3?14:row>=1 && row<=5?4:0;
 case '&': { static const uint8_t v[7]={12,18,20,8,21,18,13}; return v[row]; }
 default: { static const uint8_t v[7]={14,17,1,2,4,0,4}; return v[row]; }
 }
}
static void color(unsigned rgb) {
 SetRenderDrawColor(renderer,(uint8_t)(rgb>>16),(uint8_t)(rgb>>8),(uint8_t)rgb,255);
}
static void box(int x,int y,int w,int h,unsigned rgb) {
 Rect r={x,y,w,h}; color(rgb); RenderFillRect(renderer,&r);
}
static void text(int x,int y,int scale,const char *s,unsigned rgb,int maxchars) {
 color(rgb);
 for(int i=0;s[i] && i<maxchars;i++)
  for(int row=0;row<7;row++) for(int col=0;col<5;col++)
   if(glyph(s[i],row)&(1u<<(4-col))) {
    Rect r={x+(i*6+col)*scale,y+row*scale,scale,scale};
    RenderFillRect(renderer,&r);
   }
}
static void draw(int selected,int connected,int starting) {
 color(0x101626); RenderClear(renderer);
 box(72,64,8,58,0xffd84a);
 text(100,74,6,"ATARI GAMES",0xffd84a,30);
 char label[100];
 snprintf(label,sizeof label,"%d GAME%s  /  PAGE %d OF %d",count,count==1?"":"S",
          selected/ROWS+1,(count+ROWS-1)/ROWS);
 text(100,140,2,label,0x9faac4,70);
 int first=selected/ROWS*ROWS;
 for(int row=0;row<ROWS && first+row<count;row++) {
  int i=first+row, y=190+row*62;
  if(i==selected) box(72,y-13,1136,53,0xffd84a);
  snprintf(label,sizeof label,"%d.",i+1);
  text(96,y,3,label,i==selected?0x101626:0x8793ad,5);
  text(202,y,3,games[i].title,i==selected?0x101626:0xf2f4fa,49);
 }
 box(72,596,1136,2,0x303c54);
 text(96,624,3,starting?"STARTING GAME...":
      connected?"UP / DOWN TO CHOOSE    FIRE TO PLAY":"CONNECT A JOYSTICK TO CHOOSE A GAME",
      starting?0xffd84a:0xf2f4fa,60);
 text(96,668,2,"RESTART THE CONSOLE TO CHOOSE ANOTHER GAME",0x8793ad,80);
 RenderPresent(renderer);
}
static int cartridge(void) {
 FILE *f=fopen(KEYS,"r"); unsigned value; char extra;
 if(!f) return -1;
 int n=fscanf(f,"%u %c",&value,&extra); fclose(f);
 if(n!=1 || value>65535) return -1;
 return (value&128)!=0;
}
static int catalog(const char *path) {
 FILE *f=fopen(path,"r"); if(!f) return -1;
 char line[256];
 if(!fgets(line,sizeof line,f) || strcmp(line,"ATARI_MENU_V1\n")) goto bad;
 while(fgets(line,sizeof line,f)) {
  size_t n=strlen(line);
  if(count==LIMIT || n<72 || line[n-1]!='\n') goto bad;
  line[--n]=0;
  if(line[64]!='\t' || line[69]!='\t') goto bad;
  for(int j=0;j<64;j++) if(!strchr("0123456789abcdef",line[j])) goto bad;
  if(strncmp(line+65,"2600",4) && strncmp(line+65,"7800",4)) goto bad;
  if(n-70<1 || n-70>96) goto bad;
  for(size_t j=70;j<n;j++) if((unsigned char)line[j]<32 || (unsigned char)line[j]>126) goto bad;
  for(int j=0;j<count;j++) if(!strncmp(games[j].id,line,64)) goto bad;
  memcpy(games[count].id,line,64);
  games[count].system=atoi(line+65);
  memcpy(games[count].title,line+70,n-70+1);
  count++;
 }
 if(ferror(f) || !count) goto bad;
 fclose(f); return 0;
bad:
 fclose(f); fprintf(stderr,"Menu catalog invalid\n"); return -1;
}
static void stop(int sig) { (void)sig; stopped=1; }
static void close_pad(Pad *p) {
 if(p->fd>=0) close(p->fd);
 p->fd=-1; p->axis=0;
}
static int read_pad(Pad *p,int *fire_pressed) {
 JoyEvent e; ssize_t n;
 for(int i=0;i<256;i++) {
  n=read(p->fd,&e,sizeof e);
  if(n<0 && (errno==EAGAIN || errno==EINTR)) return 0;
  if(n!=(ssize_t)sizeof e) {
   fprintf(stderr,"Joystick %d disconnected or read failed (%ld, errno %d)\n",p->port,(long)n,errno);
   close_pad(p); return -1;
  }
  /* Axis 1 is vertical on the stock Linux DE9 joystick driver. Synthetic
   * snapshots can report an uninitialized extreme until the first movement.
   * Stay neutral until a real change, also after a queue-overflow snapshot. */
  if((e.type&0x7f)==2 && e.number==1) {
   if(p->logged_axes<32) {
    fprintf(stderr,"Joystick %d vertical value %d%s at %u ms\n",
            p->port,e.value,(e.type&0x80)?" (initial state, ignored)":"",e.time);
    p->logged_axes++;
   }
   p->axis=(e.type&0x80)?0:e.value;
  }
  if((e.type&0x7f)==1) {
   if(p->logged_buttons<64 && (!(e.type&0x80) || e.value)) {
    fprintf(stderr,"Joystick %d button %u value %d%s at %u ms\n",
            p->port,e.number,e.value,(e.type&0x80)?" (initial state)":"",e.time);
    p->logged_buttons++;
   }
   /* Every real press is an action, independent of other buttons/ports.
    * Keep it latched through this drain even if its release is queued too.
    * Synthetic initial-state events must never launch a game. */
   if(!(e.type&0x80) && e.value==1) *fire_pressed=1;
  }
 }
 return 0;
}
static int selected_file(const char *path,int selected) {
 char temp[512];
 if(snprintf(temp,sizeof temp,"%s.new",path)>=(int)sizeof temp) return -1;
 FILE *f=fopen(temp,"w"); if(!f) return -1;
 int ok=fprintf(f,"%s\n",games[selected].id)>0;
 if(fclose(f)) ok=0;
 if(!ok || rename(temp,path)) { unlink(temp); return -1; }
 fprintf(stderr,"Menu selected: %s (%s)\n",games[selected].title,games[selected].id);
 return 0;
}
int main(int argc,char **argv) {
 int result=1, selected=0, dirty=1, previous_direction=0;
 uint32_t next_repeat=0, last_probe=0, last_keys=0, last_draw=0;
 Pad pads[2]={{.fd=-1,.port=0},{.fd=-1,.port=1}};
 if(argc!=3 || catalog(argv[1])) return 1;
 int cart=cartridge(); if(cart!=0) return cart>0?2:1;
 signal(SIGINT,stop); signal(SIGTERM,stop); signal(SIGHUP,stop);
 library=dlopen("libSDL2-2.0.so.0",RTLD_NOW|RTLD_LOCAL);
 if(!library) { fprintf(stderr,"SDL2 load failed: %s\n",dlerror()); return 1; }
#define LOAD(name) do { *(void **)(&name)=dlsym(library,"SDL_" #name); \
 if(!name) { fprintf(stderr,"Missing SDL_" #name "\n"); goto done; } } while(0)
 LOAD(Init); LOAD(SetHint); LOAD(GetError); LOAD(CreateWindow); LOAD(CreateRenderer);
 LOAD(SetRenderDrawColor); LOAD(RenderClear); LOAD(RenderFillRect);
 LOAD(RenderSetLogicalSize); LOAD(RenderPresent); LOAD(DestroyRenderer);
 LOAD(DestroyWindow); LOAD(Quit); LOAD(Delay); LOAD(PumpEvents); LOAD(GetTicks);
#undef LOAD
 SetHint("SDL_RENDER_SCALE_QUALITY","0");
 if(Init(0x20)!=0) goto sdl_error;
 /* Match the stock frontend's 1280x720 shown window and accelerated renderer. */
 window=CreateWindow("Atari Games",0x1fff0000,0x1fff0000,1280,720,4);
 if(!window) goto sdl_error;
 renderer=CreateRenderer(window,-1,6);
 if(!renderer) renderer=CreateRenderer(window,-1,1);
 if(!renderer || RenderSetLogicalSize(renderer,1280,720)) goto sdl_error;
 fprintf(stderr,"Game menu ready: %d entries, idle redraw every %d ms\n",count,IDLE_REDRAW_MS);
 last_probe=GetTicks()-1000;
 while(!stopped) {
  uint32_t now=GetTicks();
  PumpEvents();
  if(now-last_keys>=200) {
   last_keys=now; cart=cartridge();
   if(cart!=0) { result=cart>0?2:1; break; }
  }
  if(now-last_probe>=1000) {
   last_probe=now;
   for(int i=0;i<2;i++) if(pads[i].fd<0) {
    char path[32]; snprintf(path,sizeof path,"/dev/input/js%d",i);
    pads[i].fd=open(path,O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if(pads[i].fd>=0) {
     /* JSIOCGNAME(128), the same ARM Linux ioctl used by the stock frontend. */
     char name[128]={0};
     if(ioctl(pads[i].fd,0x80806a13UL,name)<0) snprintf(name,sizeof name,"unknown");
     name[sizeof name-1]=0;
     fprintf(stderr,"Opened joystick %d: %s\n",i,name);
     dirty=1;
    }
   }
  }
  int direction=0, fire=0, connected=0;
  for(int i=0;i<2;i++) if(pads[i].fd>=0) {
   if(read_pad(&pads[i],&fire)) { dirty=1; continue; }
   connected++;
   if(pads[i].axis < -16000) direction=-1;
   if(pads[i].axis > 16000) direction=1;
  }
  if(direction && (direction!=previous_direction || (int32_t)(now-next_repeat)>=0)) {
   selected=(selected+direction+count)%count; dirty=1;
   next_repeat=now+(direction!=previous_direction?400:150);
  }
  previous_direction=direction;
  if(fire) {
   cart=cartridge();
   if(cart==0) {
    /* Present a complete frame; SDL does not retain the backbuffer contents
     * after presentation. Leave enough time to read it before teardown. */
    draw(selected,connected,1);
    Delay(800);
    cart=cartridge();
   }
   result=cart!=0?(cart>0?2:1):(selected_file(argv[2],selected)?1:0);
   break;
  }
  /* The display may still be settling after the boot screen's mode change.
   * Present complete frames periodically even without input, so one missed
   * startup frame cannot leave the selector invisible indefinitely. Keep
   * input-triggered drawing immediate and idle drawing at a modest cadence. */
  if(dirty || now-last_draw>=IDLE_REDRAW_MS) {
   draw(selected,connected,0); dirty=0; last_draw=GetTicks();
  }
  Delay(16);
 }
 goto done;
sdl_error:
 fprintf(stderr,"Menu display failed: %s\n",GetError());
done:
 for(int i=0;i<2;i++) close_pad(&pads[i]);
 if(renderer) { fprintf(stderr,"Menu closing renderer\n"); DestroyRenderer(renderer); }
 if(window) { fprintf(stderr,"Menu closing window\n"); DestroyWindow(window); }
 if(Quit) { fprintf(stderr,"Menu closing SDL\n"); Quit(); }
 if(library) dlclose(library);
 fprintf(stderr,"Menu exiting with status %d\n",result);
 return result;
}
