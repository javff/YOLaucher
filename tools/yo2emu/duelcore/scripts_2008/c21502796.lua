--ライトロード・ハンター ライコウ
--Ryko, Lightsworn Hunter (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e; el cliente lo llama «Raiko», del japones): "FLIP: You can destroy 1
--card on the field. Send the top 3 cards of your Deck to the Graveyard."
--Diferencias con CardScripts (errata moderna: "Apply these effects (simultaneously)"): en 2008 la destruccion SELECCIONA
--OBJETIVO al activarse (el original japones dice «seleccionar»), la destruccion se resuelve primero y LUEGO se mandan
--las 3 cartas del Deck al Cementerio (despues de la destruccion; NO se usa Duel.BreakEffect: medido en ocgcore, con
--BreakEffect el efecto «when» de la carta destruida, p. ej. Poison Draw Frog, pierde el momento).
local s,id=GetID()
function s.initial_effect(c)
	--flip
	local e1=Effect.CreateEffect(c)
	e1:SetDescription(aux.Stringid(id,0))
	e1:SetCategory(CATEGORY_DECKDES+CATEGORY_DESTROY)
	e1:SetType(EFFECT_TYPE_SINGLE+EFFECT_TYPE_FLIP)
	e1:SetProperty(EFFECT_FLAG_CARD_TARGET)
	e1:SetTarget(s.target)
	e1:SetOperation(s.operation)
	c:RegisterEffect(e1)
end
function s.target(e,tp,eg,ep,ev,re,r,rp,chk,chkc)
	if chkc then return chkc:IsOnField() end
	if chk==0 then return true end
	Duel.SetOperationInfo(0,CATEGORY_DECKDES,nil,0,tp,3)
	--"You can destroy": se elige objetivo solo si el jugador quiere
	if Duel.IsExistingTarget(aux.TRUE,tp,LOCATION_ONFIELD,LOCATION_ONFIELD,1,nil)
		and Duel.SelectYesNo(tp,aux.Stringid(id,1)) then
		Duel.Hint(HINT_SELECTMSG,tp,HINTMSG_DESTROY)
		local g=Duel.SelectTarget(tp,aux.TRUE,tp,LOCATION_ONFIELD,LOCATION_ONFIELD,1,1,nil)
		Duel.SetOperationInfo(0,CATEGORY_DESTROY,g,#g,0,0)
	end
end
function s.operation(e,tp,eg,ep,ev,re,r,rp)
	local tc=Duel.GetFirstTarget()
	if tc and tc:IsRelateToEffect(e) then
		Duel.Destroy(tc,REASON_EFFECT)
	end
	Duel.DiscardDeck(tp,3,REASON_EFFECT)
end
