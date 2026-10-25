--死のデッキ破壊ウイルス
--Crush Card Virus (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "Tribute 1 DARK monster with 1000 or less ATK. Check all monsters
--your opponent controls, your opponent's hand, and all cards they draw (until the end of your opponent's 3rd turn after
--this card's activation), and destroy all monsters with 1500 or more ATK."
--Diferencias con CardScripts (errata moderna: sin dano al rival hasta el final del turno siguiente, ver SOLO la mano y
--los monstruos al resolver, y luego el rival PUEDE destruir hasta 3 monstruos de 1500+ de su Deck): en 2008 (1) se
--puede activar siempre (no exige que haya algo que destruir); (2) al resolver se mira y se destruye lo de 1500+ de ATK
--en el campo y la mano del rival; (3) durante los 3 turnos siguientes del rival cada carta que ROBE se le revela a su
--oponente y se destruye si es un monstruo de 1500+ de ATK (no se mira lo que invoque despues ni su Deck); (4) sin
--proteccion de dano para el rival. Cuenta de turnos: CHINT_TURN en la carta (1, 2, 3 al final de cada turno del rival),
--que el traductor manda como 0x3b con P2 (NOTES §34).
local s,id=GetID()
function s.initial_effect(c)
	--Activate
	local e1=Effect.CreateEffect(c)
	e1:SetCategory(CATEGORY_DESTROY)
	e1:SetType(EFFECT_TYPE_ACTIVATE)
	e1:SetCode(EVENT_FREE_CHAIN)
	e1:SetHintTiming(0,TIMING_TOHAND)
	e1:SetCost(s.cost)
	e1:SetOperation(s.activate)
	c:RegisterEffect(e1)
end
function s.costfilter(c)
	return c:IsAttribute(ATTRIBUTE_DARK) and c:IsAttackBelow(1000)
end
function s.cost(e,tp,eg,ep,ev,re,r,rp,chk)
	if chk==0 then return Duel.CheckReleaseGroupCost(tp,s.costfilter,1,false,nil,nil) end
	local g=Duel.SelectReleaseGroupCost(tp,s.costfilter,1,1,false,nil,nil)
	Duel.Release(g,REASON_COST)
end
function s.virus(c)
	return c:IsMonster() and c:IsAttackAbove(1500)
end
function s.activate(e,tp,eg,ep,ev,re,r,rp)
	local c=e:GetHandler()
	--(2) mirar los monstruos del rival y su mano
	local conf=Duel.GetFieldGroup(tp,0,LOCATION_MZONE|LOCATION_HAND)
	if #conf>0 then
		Duel.ConfirmCards(tp,conf)
		Duel.Destroy(conf:Filter(s.virus,nil),REASON_EFFECT)
		Duel.ShuffleHand(1-tp)
	end
	--(3) hasta el final del 3.er turno del rival despues de la activacion (si se activa en el turno del rival, ese
	--turno no cuenta: el contador de reset lo cuenta al acabar, por eso 4)
	local turnos=3
	if Duel.GetTurnPlayer()==1-tp then turnos=4 end
	local turno_act=Duel.GetTurnCount()
	local n=0
	local e1=Effect.CreateEffect(c)
	e1:SetType(EFFECT_TYPE_FIELD+EFFECT_TYPE_CONTINUOUS)
	e1:SetCode(EVENT_DRAW)
	e1:SetCondition(function(e,tp,eg,ep,ev,re,r,rp) return ep~=tp end)
	e1:SetOperation(s.drawop)
	e1:SetReset(RESET_PHASE|PHASE_END|RESET_OPPO_TURN,turnos)
	Duel.RegisterEffect(e1,tp)
	--contador visible (1, 2, 3) al final de cada turno del rival
	local e2=Effect.CreateEffect(c)
	e2:SetType(EFFECT_TYPE_FIELD+EFFECT_TYPE_CONTINUOUS)
	e2:SetCode(EVENT_PHASE+PHASE_END)
	e2:SetCountLimit(1)
	e2:SetCondition(function(e,tp,eg,ep,ev,re,r,rp) return Duel.GetTurnPlayer()~=tp and Duel.GetTurnCount()~=turno_act end)
	e2:SetOperation(function(e,tp,eg,ep,ev,re,r,rp)
		n=n+1
		c:SetTurnCounter(n)
	end)
	e2:SetReset(RESET_PHASE|PHASE_END|RESET_OPPO_TURN,turnos)
	Duel.RegisterEffect(e2,tp)
end
function s.drawop(e,tp,eg,ep,ev,re,r,rp)
	local dg=eg:Filter(Card.IsLocation,nil,LOCATION_HAND)
	if #dg==0 then return end
	Duel.Hint(HINT_CARD,0,id)
	Duel.ConfirmCards(tp,dg)
	local vg=dg:Filter(s.virus,nil)
	if #vg>0 then Duel.Destroy(vg,REASON_EFFECT) end
	Duel.ShuffleHand(1-tp)
end
