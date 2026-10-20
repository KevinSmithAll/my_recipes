(() => {
  const downloadButton = document.querySelector("#download-week-image");
  const dayCards = [...document.querySelectorAll(".plan-day")];
  if (!downloadButton || dayCards.length === 0) return;

  const canvasWidth = 1400;
  const margin = 56;
  const gap = 18;
  const cardPadding = 24;
  const lineHeight = 34;
  const mealTitleHeight = 40;
  const dayHeaderHeight = 48;
  const cardWidth = canvasWidth - margin * 2;
  const mealWidth = (cardWidth - cardPadding * 2 - gap * 2) / 3;
  const textWidth = mealWidth - 24;
  const colors = {
    background: "#f3f5f1",
    card: "#ffffff",
    ink: "#26312d",
    muted: "#66736e",
    accent: "#2e6a57",
    line: "#dce3dc",
  };
  const regularFont = '24px system-ui, -apple-system, "Segoe UI", sans-serif';
  const dayFont = '700 30px Georgia, "Times New Roman", serif';
  const mealFont = '700 24px system-ui, -apple-system, "Segoe UI", sans-serif';

  function wrapText(context, text, maxWidth) {
    const words = text.split(/\s+/).flatMap((word) => {
      if (context.measureText(word).width <= maxWidth) return [word];
      const pieces = [];
      let piece = "";
      for (const character of word) {
        if (piece && context.measureText(piece + character).width > maxWidth) {
          pieces.push(piece);
          piece = character;
        } else {
          piece += character;
        }
      }
      if (piece) pieces.push(piece);
      return pieces;
    });
    const lines = [];
    let line = "";
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (line && context.measureText(candidate).width > maxWidth) {
        lines.push(line);
        line = word;
      } else {
        line = candidate;
      }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [""];
  }

  function portionsLabel(value) {
    const lastTwo = value % 100;
    const last = value % 10;
    const word = lastTwo >= 11 && lastTwo <= 14
      ? "порций"
      : last === 1 ? "порция" : last >= 2 && last <= 4 ? "порции" : "порций";
    return `${value} ${word}`;
  }

  function collectDays(context) {
    return dayCards.map((card) => ({
      title: card.querySelector("h2").innerText.replace(/\s+/g, " ").trim(),
      meals: [...card.querySelectorAll(".plan-meal")].map((meal) => ({
        title: meal.querySelector("legend").innerText.trim(),
        people: [...meal.querySelectorAll(".plan-person")].map((person) => {
          const personName = person.querySelector(":scope > span").innerText.trim();
          const select = person.querySelector("select");
          const portions = Number(person.querySelector('input[type="number"]').value);
          let mealName = select.value === "dash"
            ? "—"
            : select.value
              ? select.selectedOptions[0].text.trim()
              : "Не запланировано";
          if (select.value && select.value !== "dash") {
            mealName += ` · ${portionsLabel(portions)}`;
          }
          return { text: `${personName}: ${mealName}` };
        }),
      })),
    }));
  }

  function measureMeal(context, meal) {
    context.font = regularFont;
    const rowHeights = meal.people.map((person) =>
      wrapText(context, person.text, textWidth).length * lineHeight);
    return mealTitleHeight + rowHeights.reduce((sum, height) => sum + height + 12, 0);
  }

  function drawWeek() {
    const measureCanvas = document.createElement("canvas");
    const measureContext = measureCanvas.getContext("2d");
    if (!measureContext) throw new Error("Браузер не поддерживает создание изображения.");
    const days = collectDays(measureContext);
    const dayHeights = days.map((day) =>
      dayHeaderHeight + Math.max(...day.meals.map((meal) => measureMeal(measureContext, meal)))
      + cardPadding * 2);
    const titleHeight = 112;
    const bottomMargin = 48;
    const canvasHeight = titleHeight + dayHeights.reduce((sum, height) => sum + height + gap, 0)
      - gap + bottomMargin;
    const canvas = document.createElement("canvas");
    canvas.width = canvasWidth;
    canvas.height = canvasHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Не удалось подготовить изображение.");

    context.fillStyle = colors.background;
    context.fillRect(0, 0, canvasWidth, canvasHeight);
    context.fillStyle = colors.accent;
    context.fillRect(0, 0, 12, canvasHeight);
    context.fillStyle = colors.ink;
    context.font = dayFont;
    context.fillText("План питания", margin, 54);
    context.fillStyle = colors.muted;
    context.font = regularFont;
    const weekStart = dayCards[0].querySelector(".day-content select").name.slice(0, 10);
    const weekEndDate = new Date(`${weekStart}T00:00:00`);
    weekEndDate.setDate(weekEndDate.getDate() + 6);
    const dateFormatter = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" });
    context.fillText(
      `${dateFormatter.format(new Date(`${weekStart}T00:00:00`))} — ${dateFormatter.format(weekEndDate)}`,
      margin,
      91,
    );
    let top = titleHeight;
    days.forEach((day, dayIndex) => {
      const height = dayHeights[dayIndex];
      context.fillStyle = colors.card;
      context.fillRect(margin, top, cardWidth, height);
      context.fillStyle = colors.accent;
      context.fillRect(margin, top + 16, 7, height - 32);
      context.fillStyle = colors.ink;
      context.font = dayFont;
      context.fillText(day.title, margin + cardPadding, top + 42);

      day.meals.forEach((meal, mealIndex) => {
        const left = margin + cardPadding + mealIndex * (mealWidth + gap);
        let y = top + dayHeaderHeight;
        context.fillStyle = colors.line;
        context.fillRect(left - 12, y - 20, mealWidth, 1);
        context.fillStyle = colors.accent;
        context.font = mealFont;
        context.fillText(meal.title, left, y + 10);
        y += mealTitleHeight;
        context.font = regularFont;
        meal.people.forEach((person) => {
          const lines = wrapText(context, person.text, textWidth);
          context.fillStyle = person.text.endsWith("Не запланировано")
            ? colors.muted
            : colors.ink;
          lines.forEach((line) => {
            context.fillText(line, left, y + 20);
            y += lineHeight;
          });
          y += 12;
        });
      });
      top += height + gap;
    });
    return canvas;
  }

  downloadButton.addEventListener("click", () => {
    try {
      const canvas = drawWeek();
      canvas.toBlob((blob) => {
        if (!blob) {
          window.alert("Не удалось создать PNG-файл.");
          return;
        }
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `meal-plan-${dayCards[0].querySelector(".day-content select").name.slice(0, 10)}.png`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      }, "image/png");
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Не удалось создать изображение.");
    }
  });
})();
