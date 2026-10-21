/*
 * The maintenance page of the target stock: a target that is already in place is locked.
 *
 *   <input data-soll disabled …>  <input type="hidden" data-freigabe …>  <button data-frei>
 *
 * "Edit" releases exactly its row: the field becomes usable, the release
 * goes along with the form, and only with it does the server read the value
 * ('SollPflegeForm'). That way a slip needs two steps.
 */
document.addEventListener("click", (ereignis) => {
  const knopf = ereignis.target.closest("[data-frei]");
  if (!knopf) return;
  const zeile = knopf.closest("tr");
  const eingabe = zeile.querySelector("input[data-soll]");
  const freigabe = zeile.querySelector("input[data-freigabe]");
  eingabe.disabled = false;
  freigabe.value = "1";
  knopf.hidden = true;
  zeile.classList.add("tabellenzeile-auffaellig");
  eingabe.focus();
  eingabe.select();
});
