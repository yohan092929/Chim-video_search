document.addEventListener("DOMContentLoaded", () => {
  const searchForm = document.getElementById("search-form");
  const searchInput = document.getElementById("search-input");
  const clearBtn = document.getElementById("clear-btn");
  const videoPlayer = document.getElementById("video-player");
  const subtitlesTrack = document.getElementById("subtitles-track");

  let currentVideoId = null;
  let playlist = []; // Array of cross-video HighlightClip objects
  let currentIndex = -1;
  let isHighlightMode = false;
  let animFrameId = null;

  const defaultPlaceholder = "검색어를 입력하고 Enter를 누르면 전체 영상 하이라이트가 자동 연속 재생됩니다...";

  // 1. Preload initial video if available for clean player display
  async function loadInitialVideo() {
    try {
      const res = await fetch("/api/videos");
      if (!res.ok) return;
      const data = await res.json();
      if (data.videos && data.videos.length > 0) {
        const first = data.videos[0];
        currentVideoId = first.video_id;
        videoPlayer.src = first.stream_url;
        if (subtitlesTrack) {
          subtitlesTrack.src = `/api/videos/${first.video_id}/subtitles.vtt`;
        }
      }
    } catch (e) {
      console.warn("Initial video fetch:", e);
    }
  }

  // 2. Play clip by index across any video in playlist
  function playClip(index) {
    if (!playlist || playlist.length === 0) return;
    if (index < 0 || index >= playlist.length) {
      // Completed all clips
      isHighlightMode = false;
      currentIndex = -1;
      videoPlayer.pause();
      searchInput.placeholder = `하이라이트 연속 재생 완료 (${playlist.length}개 클립)`;
      setTimeout(() => { searchInput.placeholder = defaultPlaceholder; }, 3500);
      return;
    }

    currentIndex = index;
    isHighlightMode = true;
    const clip = playlist[currentIndex];

    searchInput.placeholder = `▶ [${currentIndex + 1}/${playlist.length}] ${clip.video_title}`;

    const isDifferentVideo = (currentVideoId !== clip.video_id);
    currentVideoId = clip.video_id;

    function seekAndStart() {
      videoPlayer.currentTime = clip.start;
      videoPlayer.play().catch(err => {
        console.warn("Playback started with user interaction required:", err);
      });
      startBoundaryTracking();
    }

    if (isDifferentVideo || !videoPlayer.src) {
      videoPlayer.src = clip.stream_url;
      if (subtitlesTrack) {
        subtitlesTrack.src = clip.subtitles_url;
      }
      const onLoaded = () => {
        videoPlayer.removeEventListener("loadedmetadata", onLoaded);
        seekAndStart();
      };
      videoPlayer.addEventListener("loadedmetadata", onLoaded, { once: true });
      videoPlayer.load();
    } else {
      seekAndStart();
    }
  }

  // 3. Millisecond-accurate boundary tracking without visual overlay
  function startBoundaryTracking() {
    if (animFrameId) cancelAnimationFrame(animFrameId);

    function checkBoundary() {
      if (!isHighlightMode || currentIndex < 0 || currentIndex >= playlist.length) {
        return;
      }

      const clip = playlist[currentIndex];
      const curTime = videoPlayer.currentTime;

      // Check if clip has reached its end boundary
      if (curTime >= clip.end) {
        // Automatically leap to next clip consecutively
        playClip(currentIndex + 1);
        return;
      }

      animFrameId = requestAnimationFrame(checkBoundary);
    }

    animFrameId = requestAnimationFrame(checkBoundary);
  }

  // 4. Global Search Submit (Zero-Click Consecutive Autoplay)
  searchForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const keyword = searchInput.value.trim();
    if (!keyword) return;

    searchInput.blur();
    searchInput.placeholder = `"${keyword}" 전체 영상에서 하이라이트 추출 중...`;

    try {
      const res = await fetch(`/api/search?keyword=${encodeURIComponent(keyword)}`);
      if (!res.ok) throw new Error("검색 요청에 실패했습니다.");
      const data = await res.json();

      if (data.clips && data.clips.length > 0) {
        playlist = data.clips;
        // Zero-click: automatically start consecutive autoplay of clip 0
        playClip(0);
      } else {
        isHighlightMode = false;
        playlist = [];
        currentIndex = -1;
        searchInput.placeholder = `"${keyword}" 관련 검색 결과가 없습니다.`;
        setTimeout(() => { searchInput.placeholder = defaultPlaceholder; }, 2500);
      }
    } catch (err) {
      console.error("Global search error:", err);
      searchInput.placeholder = "검색 중 오류가 발생했습니다.";
      setTimeout(() => { searchInput.placeholder = defaultPlaceholder; }, 2500);
    }
  });

  // 5. Keyboard Navigation (Arrow Down/J: Next, Arrow Up/K: Prev, Space: Pause/Resume)
  document.addEventListener("keydown", (e) => {
    if (document.activeElement === searchInput) {
      return; // typing in search box
    }

    if (e.code === "ArrowDown" || e.code === "KeyJ") {
      e.preventDefault();
      if (isHighlightMode && playlist.length > 0) {
        playClip(currentIndex + 1);
      }
    } else if (e.code === "ArrowUp" || e.code === "KeyK") {
      e.preventDefault();
      if (isHighlightMode && playlist.length > 0) {
        playClip(currentIndex - 1);
      }
    } else if (e.code === "Space") {
      e.preventDefault();
      if (videoPlayer.paused) {
        videoPlayer.play();
      } else {
        videoPlayer.pause();
      }
    }
  });

  // 6. Input and clear button
  searchInput.addEventListener("input", () => {
    clearBtn.style.display = searchInput.value ? "block" : "none";
  });

  clearBtn.addEventListener("click", () => {
    searchInput.value = "";
    clearBtn.style.display = "none";
    searchInput.placeholder = defaultPlaceholder;
    isHighlightMode = false;
    playlist = [];
    currentIndex = -1;
    if (animFrameId) cancelAnimationFrame(animFrameId);
    searchInput.focus();
  });

  // Initialize
  loadInitialVideo();
});


