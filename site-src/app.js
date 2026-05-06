const state = {
  album: null,
  filteredItems: [],
  renderedCount: 0,
  batchSize: 48,
  activeIndex: -1,
  controlsOpen: false,
  isMobile: false,
};

const gallery = document.getElementById("gallery");
const sentinel = document.getElementById("sentinel");
const emptyState = document.getElementById("empty-state");
const lightbox = document.getElementById("lightbox");
const lightboxMedia = document.getElementById("lightbox-media");
const lightboxTitle = document.getElementById("lightbox-title");
const lightboxDetails = document.getElementById("lightbox-details");
const contributorFilter = document.getElementById("contributor-filter");
const mobileBar = document.getElementById("mobile-bar");
const mobileResults = document.getElementById("mobile-results");
const mobileFiltersToggle = document.getElementById("mobile-filters-toggle");
const mobileFiltersClose = document.getElementById("mobile-filters-close");
const controlsPanel = document.getElementById("controls-panel");
const controlsScrim = document.getElementById("controls-scrim");
const mobileMediaQuery = window.matchMedia("(max-width: 760px), (hover: none) and (pointer: coarse)");

const controls = {
  search: document.getElementById("search-input"),
  type: document.getElementById("type-filter"),
  contributor: contributorFilter,
  sort: document.getElementById("sort-order"),
};

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
  }).format(new Date(value));
}

function buildSummary(album) {
  const itemCount = `${album.counts.all} items`;
  const photoCount = `${album.counts.photos} photos`;
  const videoCount = `${album.counts.videos} videos`;
  const contributorCount = `${album.counts.contributors} contributors`;

  if (!album.dateRange.oldest || !album.dateRange.newest) {
    return `${itemCount}. ${photoCount}. ${videoCount}. ${contributorCount}.`;
  }

  return `${itemCount}. ${photoCount}. ${videoCount}. ${contributorCount}. From ${formatDate(album.dateRange.oldest)} to ${formatDate(album.dateRange.newest)}.`;
}

function buildCompactSummary(album) {
  return `${album.counts.all} items · ${album.counts.photos} photos · ${album.counts.videos} videos`;
}

function syncBodyScrollLock() {
  document.body.style.overflow = state.activeIndex !== -1 || state.controlsOpen ? "hidden" : "";
}

function updateMobileResults() {
  if (!state.album) {
    mobileResults.textContent = "";
    return;
  }

  const total = state.filteredItems.length;
  const allCount = state.album.counts.all;
  const type = controls.type.value;

  let label = `${total} items`;
  if (type === "photo") {
    label = `${total} photos`;
  } else if (type === "video") {
    label = `${total} videos`;
  }

  if (total !== allCount) {
    label = `${label} shown`;
  }

  mobileResults.textContent = label;
}

function closeControlsPanel() {
  state.controlsOpen = false;
  document.body.classList.remove("controls-open");
  mobileFiltersToggle.setAttribute("aria-expanded", "false");
  syncBodyScrollLock();
}

function openControlsPanel() {
  if (!state.isMobile || state.activeIndex !== -1) {
    return;
  }

  state.controlsOpen = true;
  document.body.classList.add("controls-open");
  mobileFiltersToggle.setAttribute("aria-expanded", "true");
  syncBodyScrollLock();
}

function setMobileMode(enabled) {
  state.isMobile = enabled;
  document.body.classList.toggle("is-mobile", enabled);
  mobileBar.hidden = !enabled;
  controlsScrim.hidden = !enabled;

  if (!enabled) {
    closeControlsPanel();
  }

  if (state.album) {
    updateHero(state.album);
    updateMobileResults();
  }
}

function updateHero(album) {
  document.title = `${album.albumTitle} - Shared Album`;
  document.getElementById("album-title").textContent = album.albumTitle;
  document.getElementById("album-summary").textContent = state.isMobile
    ? buildCompactSummary(album)
    : buildSummary(album);
}

function populateContributors(album) {
  const names = album.contributors.map((entry) => entry.name);
  contributorFilter.innerHTML = '<option value="all">Everyone</option>';

  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    contributorFilter.append(option);
  }
}

function applyFilters() {
  if (!state.album) {
    return;
  }

  const search = controls.search.value.trim().toLowerCase();
  const type = controls.type.value;
  const contributor = controls.contributor.value;
  const sortOrder = controls.sort.value;

  let items = [...state.album.items];

  if (type !== "all") {
    items = items.filter((item) => item.type === type);
  }

  if (contributor !== "all") {
    items = items.filter((item) => item.contributor === contributor);
  }

  if (search) {
    items = items.filter((item) => {
      const haystack = [item.caption, item.contributor, item.type]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(search);
    });
  }

  items.sort((left, right) => {
    if (sortOrder === "oldest") {
      return left.dateCreated.localeCompare(right.dateCreated);
    }
    return right.dateCreated.localeCompare(left.dateCreated);
  });

  state.filteredItems = items;
  state.renderedCount = 0;
  gallery.innerHTML = "";
  emptyState.hidden = items.length > 0;
  updateMobileResults();
  renderNextBatch();
}

function createCard(item, index) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "card";
  button.dataset.index = String(index);
  button.setAttribute(
    "aria-label",
    `${item.type} from ${item.contributor} on ${formatDate(item.dateCreated)}`,
  );

  const media = document.createElement("div");
  media.className = "card__media";

  const image = document.createElement("img");
  image.className = "card__image";
  image.src = item.thumb;
  image.alt = item.caption || `${item.type} from ${item.contributor}`;
  image.loading = "lazy";
  image.decoding = "async";
  image.width = item.thumbWidth;
  image.height = item.thumbHeight;
  media.append(image);

  if (item.type === "video") {
    const badge = document.createElement("span");
    badge.className = "card__badge";
    badge.textContent = "Video";
    media.append(badge);
  }
  button.append(media);

  button.addEventListener("click", () => openLightbox(index));
  return button;
}

function renderNextBatch() {
  if (!state.filteredItems.length) {
    return;
  }

  const nextItems = state.filteredItems.slice(
    state.renderedCount,
    state.renderedCount + state.batchSize,
  );

  const fragment = document.createDocumentFragment();
  nextItems.forEach((item, offset) => {
    fragment.append(createCard(item, state.renderedCount + offset));
  });
  gallery.append(fragment);
  state.renderedCount += nextItems.length;
}

function buildLightboxDetail(item) {
  return `${item.contributor} · ${formatDate(item.dateCreated)}${item.type === "video" ? " · Video" : ""}`;
}

function openLightbox(index) {
  const item = state.filteredItems[index];
  if (!item) {
    return;
  }

  state.activeIndex = index;
  closeControlsPanel();
  lightbox.hidden = false;
  lightboxMedia.innerHTML = "";

  if (item.type === "video") {
    const video = document.createElement("video");
    video.controls = true;
    video.preload = "metadata";
    video.poster = item.poster;
    video.src = item.src;
    lightboxMedia.append(video);
  } else {
    const image = document.createElement("img");
    image.src = item.src;
    image.alt = item.caption || `${item.type} from ${item.contributor}`;
    image.width = item.width;
    image.height = item.height;
    image.addEventListener("click", () => moveLightbox(1));
    lightboxMedia.append(image);
  }

  lightboxTitle.hidden = !item.caption;
  lightboxTitle.textContent = item.caption || "";
  lightboxDetails.textContent = buildLightboxDetail(item);
  syncBodyScrollLock();
}

function closeLightbox() {
  lightbox.hidden = true;
  lightboxMedia.innerHTML = "";
  state.activeIndex = -1;
  syncBodyScrollLock();
}

function moveLightbox(direction) {
  if (state.activeIndex === -1) {
    return;
  }

  const nextIndex = state.activeIndex + direction;
  if (nextIndex < 0 || nextIndex >= state.filteredItems.length) {
    return;
  }

  openLightbox(nextIndex);
}

async function loadAlbum() {
  const response = await fetch("album.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load album manifest: ${response.status}`);
  }

  const album = await response.json();
  state.album = album;
  updateHero(album);
  populateContributors(album);
  applyFilters();
}

function wireControls() {
  setMobileMode(mobileMediaQuery.matches);

  Object.values(controls).forEach((node) => {
    node.addEventListener("input", applyFilters);
    node.addEventListener("change", applyFilters);
  });

  mobileFiltersToggle.addEventListener("click", () => {
    if (state.controlsOpen) {
      closeControlsPanel();
    } else {
      openControlsPanel();
    }
  });
  mobileFiltersClose.addEventListener("click", closeControlsPanel);
  controlsScrim.addEventListener("click", closeControlsPanel);
  document.getElementById("prev-item").addEventListener("click", () => moveLightbox(-1));
  document.getElementById("next-item").addEventListener("click", () => moveLightbox(1));

  lightbox.querySelectorAll("[data-close]").forEach((node) => {
    node.addEventListener("click", closeLightbox);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.controlsOpen) {
      closeControlsPanel();
      return;
    }

    if (lightbox.hidden) {
      return;
    }

    if (event.key === "Escape") {
      closeLightbox();
    } else if (event.key === "ArrowLeft") {
      moveLightbox(-1);
    } else if (event.key === "ArrowRight") {
      moveLightbox(1);
    }
  });

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        renderNextBatch();
      }
    });
  }, { rootMargin: "1200px 0px" });

  observer.observe(sentinel);

  if (typeof mobileMediaQuery.addEventListener === "function") {
    mobileMediaQuery.addEventListener("change", (event) => setMobileMode(event.matches));
  } else {
    mobileMediaQuery.addListener((event) => setMobileMode(event.matches));
  }
}

wireControls();
loadAlbum().catch((error) => {
  document.getElementById("album-title").textContent = "Album failed to load";
  document.getElementById("album-summary").textContent = error.message;
});
