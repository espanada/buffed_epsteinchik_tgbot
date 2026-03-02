(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  const bio = document.getElementById("bio");
  const bioCounter = document.getElementById("bioCounter");
  const looking = document.getElementById("looking");
  const minAge = document.getElementById("minAge");
  const maxAge = document.getElementById("maxAge");
  const isActive = document.getElementById("isActive");
  const saveBtn = document.getElementById("saveBtn");
  const status = document.getElementById("status");
  const params = new URLSearchParams(window.location.search);
  const apiBase = (params.get("api_base") || window.location.origin || "").replace(/\/+$/, "");

  function setStatus(text) {
    status.textContent = text;
  }

  function clampAge(value) {
    const n = Number(value);
    if (!Number.isInteger(n)) return null;
    if (n < 18 || n > 99) return null;
    return n;
  }

  function updateCounter() {
    bioCounter.textContent = `${bio.value.length}/300`;
  }

  function parseBoolParam(value) {
    if (value === "1" || value === "true") return true;
    if (value === "0" || value === "false") return false;
    return null;
  }

  function hydrateFromQuery() {
    const bioValue = params.get("bio");
    if (typeof bioValue === "string") {
      bio.value = bioValue.slice(0, 300);
    }

    const lookingValue = params.get("looking_for");
    if (lookingValue && ["any", "male", "female"].includes(lookingValue)) {
      looking.value = lookingValue;
    }

    const minRaw = params.get("min_age");
    const maxRaw = params.get("max_age");
    const minParsed = clampAge(minRaw);
    const maxParsed = clampAge(maxRaw);
    if (minParsed !== null) minAge.value = String(minParsed);
    if (maxParsed !== null) maxAge.value = String(maxParsed);

    const isActiveRaw = params.get("is_active");
    const isActiveParsed = parseBoolParam(isActiveRaw);
    if (isActiveParsed !== null) {
      isActive.checked = isActiveParsed;
    }
  }

  function applyProfile(profile) {
    if (!profile || typeof profile !== "object") return;
    if (typeof profile.bio === "string") {
      bio.value = profile.bio.slice(0, 300);
    }
    if (typeof profile.looking_for === "string" && ["any", "male", "female"].includes(profile.looking_for)) {
      looking.value = profile.looking_for;
    }
    const minParsed = clampAge(profile.min_age);
    const maxParsed = clampAge(profile.max_age);
    if (minParsed !== null) minAge.value = String(minParsed);
    if (maxParsed !== null) maxAge.value = String(maxParsed);
    if (typeof profile.is_active === "boolean") {
      isActive.checked = profile.is_active;
    }
  }

  async function loadFromApi() {
    if (!apiBase || !tg || !tg.initData) return false;
    try {
      const response = await fetch(`${apiBase}/api/miniapp/profile/load`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: tg.initData }),
      });
      if (!response.ok) return false;
      const data = await response.json();
      if (!data || data.ok !== true || data.exists !== true || !data.profile) return false;
      applyProfile(data.profile);
      updateCounter();
      return true;
    } catch {
      return false;
    }
  }

  async function saveToApi(payload) {
    if (!apiBase || !tg || !tg.initData) return false;
    try {
      const response = await fetch(`${apiBase}/api/miniapp/profile/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: tg.initData, payload }),
      });
      if (!response.ok) {
        const text = await response.text();
        setStatus(`Ошибка API: ${text.slice(0, 120)}`);
        return false;
      }
      const data = await response.json();
      if (!data || data.ok !== true) {
        setStatus("Ошибка API при сохранении.");
        return false;
      }
      if (data.profile) {
        applyProfile(data.profile);
      }
      updateCounter();
      return true;
    } catch {
      setStatus("Не удалось связаться с API.");
      return false;
    }
  }

  function validate() {
    const min = clampAge(minAge.value);
    const max = clampAge(maxAge.value);
    if (min === null || max === null) {
      return "Возраст должен быть в диапазоне 18-99.";
    }
    if (max < min) {
      return "Максимальный возраст должен быть не меньше минимального.";
    }
    if (bio.value.trim().length > 0 && bio.value.trim().length < 10) {
      return "Bio должен быть не короче 10 символов.";
    }
    return "";
  }

  async function send() {
    const err = validate();
    if (err) {
      setStatus(err);
      return;
    }

    const payload = {
      bio: bio.value.trim(),
      looking_for: looking.value,
      min_age: Number(minAge.value),
      max_age: Number(maxAge.value),
      is_active: Boolean(isActive.checked),
    };

    saveBtn.disabled = true;
    const savedViaApi = await saveToApi(payload);
    if (savedViaApi) {
      setStatus("Сохранено в БД.");
    } else if (tg && typeof tg.sendData === "function") {
      tg.sendData(JSON.stringify(payload));
      setStatus("Данные отправлены. Проверь ответ бота в чате.");
    } else {
      setStatus("Открой mini app из Telegram, чтобы сохранить изменения.");
    }
    setTimeout(() => {
      saveBtn.disabled = false;
    }, 1200);
  }

  bio.addEventListener("input", updateCounter);
  saveBtn.addEventListener("click", () => {
    void send();
  });
  hydrateFromQuery();
  updateCounter();

  if (tg) {
    tg.ready();
    tg.expand();
    setStatus("Загрузка профиля...");
    loadFromApi().then((loaded) => {
      setStatus(loaded ? "Профиль загружен из БД." : "Готово к отправке.");
    });
  } else {
    setStatus("Режим предпросмотра в браузере.");
  }
})();
